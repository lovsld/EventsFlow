"""Narrative Observer (N1) — deterministic, read-only.

Implements docs/NARRATIVE_LAYER_DESIGN.md §3-§4:
  * world_log.json is the sole authority for facts (L-OBJ);
  * run_meta.json's embedded scenario snapshot supplies claim/object
    definitions (L-CTX) and provenance; summary.json is cross-checked;
  * character private files under a run (characters/*/memory.md etc.)
    are NEVER read;
  * outputs land only in the caller-chosen directory (narrative_runs/).

Products: provenance.json, observations.json, frontier.json, scenes.json,
issues.json. No timestamps anywhere in the outputs: same input -> same
bytes, always.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from . import OBSERVER_VERSION

# Design §4.3 classification table (changes require Hu's approval).
ROLE_BY_TYPE = {
    "speak": "plot", "ask": "plot", "confront": "plot", "search": "plot",
    "show": "plot", "refuse": "plot", "speak_failed": "plot",
    "move": "structure", "session_start": "structure",
    "session_end": "structure",
    "ambient": "texture", "observe": "texture", "listen": "texture",
    "work": "texture", "wait": "texture",
}

INPUT_FILES = ["world_log.json", "acp.sqlite3", "summary.json",
               "run_meta.json", "narrative.md"]
CORE_EVENT_FIELDS = {"event_id", "tick", "type", "actors", "location"}


class ObserverError(RuntimeError):
    """Raised when provenance cross-checks fail; processing stops."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_names(project_root: Path, character_ids: list[str],
                  issues: list[dict]) -> dict:
    """Display names from character profiles (L-CTX static definitions).

    Character ids are unique across the project's characters*/ dirs
    (verified 2026-11-21); scan them in sorted order, record the source
    path per id, and report ambiguity instead of guessing.
    """
    found: dict[str, list[tuple[str, str]]] = {cid: [] for cid in character_ids}
    for profile in sorted(project_root.glob("characters*/*/profile.yaml")):
        cid = name = None
        for line in profile.read_text(encoding="utf-8").splitlines():
            if line.startswith("character_id:"):
                cid = line.split(":", 1)[1].strip()
            elif line.startswith("name:"):
                name = line.split(":", 1)[1].strip()
            if cid and name:
                break
        if cid in found and name:
            found[cid].append((name, str(profile.relative_to(project_root))))
    names = {}
    for cid, cands in found.items():
        uniq = sorted({n for n, _ in cands})
        if len(uniq) == 1:
            names[cid] = {"name": uniq[0], "source": cands[0][1]}
        elif not cands:
            issues.append({"kind": "name_unresolved", "severity": "warning",
                           "detail": f"no profile found for character {cid}; "
                                     "character_id used as-is",
                           "character_id": cid})
            names[cid] = {"name": cid, "source": None}
        else:
            issues.append({"kind": "name_ambiguous", "severity": "warning",
                           "detail": f"character {cid} has differing names "
                                     f"{uniq}; character_id used as-is",
                           "character_id": cid})
            names[cid] = {"name": cid, "source": None}
    return names


def observe(run_dir: Path, out_dir: Path,
            project_root: Path | None = None) -> dict:
    """Run the Observer over one finished run directory.

    Returns a small summary dict. Raises ObserverError on provenance
    cross-check failure (design §3.2: stop and report, do not proceed).
    """
    run_dir = Path(run_dir)
    out_dir = Path(out_dir)
    if project_root is None:
        # narrative/ lives at the project root.
        project_root = Path(__file__).resolve().parent.parent
    issues: list[dict] = []

    hashes_before = {}
    for fname in INPUT_FILES:
        p = run_dir / fname
        if p.exists():
            hashes_before[fname] = _sha256(p)
    for required in ("world_log.json", "run_meta.json", "summary.json"):
        if required not in hashes_before:
            raise ObserverError(f"{run_dir}: missing required input {required}")

    meta = _load_json(run_dir / "run_meta.json")
    summary = _load_json(run_dir / "summary.json")
    world_log = _load_json(run_dir / "world_log.json")

    # ---- provenance cross-checks (design §3.2) ----
    def check(cond: bool, detail: str):
        if not cond:
            issues.append({"kind": "provenance_mismatch",
                           "severity": "error", "detail": detail})
            raise ObserverError(f"{run_dir}: {detail}")

    check(meta.get("run_id") == summary.get("run_id"),
          f"run_id mismatch: run_meta={meta.get('run_id')!r} "
          f"summary={summary.get('run_id')!r}")
    check(meta.get("scenario_id") == summary.get("scenario_id"),
          f"scenario_id mismatch: run_meta={meta.get('scenario_id')!r} "
          f"summary={summary.get('scenario_id')!r}")
    flat_events = [ev for tick_rec in world_log for ev in tick_rec["events"]]
    check(summary.get("total_events") == len(flat_events),
          f"total_events mismatch: summary={summary.get('total_events')} "
          f"world_log={len(flat_events)}")
    check(summary.get("ticks") == len(world_log),
          f"ticks mismatch: summary={summary.get('ticks')} "
          f"world_log tick records={len(world_log)}")

    scenario = meta.get("scenario") or {}
    claims_def = scenario.get("claims") or {}
    character_ids = list(meta.get("characters") or
                         sorted({a for ev in flat_events for a in ev["actors"]}))
    names = resolve_names(project_root, character_ids, issues)

    # ---- delivery reverse index (design §4.2) ----
    delivered_to: dict[str, list[str]] = {}
    muffled_for: dict[str, list[str]] = {}
    known_ids = set()
    for ev in flat_events:
        if ev["event_id"] in known_ids:
            issues.append({"kind": "duplicate_event_id", "severity": "error",
                           "detail": f"event_id {ev['event_id']} appears twice",
                           "event_id": ev["event_id"]})
        known_ids.add(ev["event_id"])
    for tick_rec in world_log:
        for d in tick_rec.get("deliveries", []):
            agent = d["agent_id"]
            for eid in d.get("delivered_event_ids", []):
                if eid not in known_ids:
                    issues.append({"kind": "delivery_unknown_event",
                                   "severity": "warning",
                                   "detail": f"delivery to {agent} references "
                                             f"unknown event {eid}",
                                   "event_id": eid})
                delivered_to.setdefault(eid, []).append(agent)
            for eid in d.get("muffled_event_ids", []):
                if eid not in known_ids:
                    issues.append({"kind": "delivery_unknown_event",
                                   "severity": "warning",
                                   "detail": f"muffled delivery to {agent} "
                                             f"references unknown event {eid}",
                                   "event_id": eid})
                muffled_for.setdefault(eid, []).append(agent)

    # ---- observations (design §4.2) ----
    time_by_tick = {rec["tick"]: rec.get("time") for rec in world_log}
    positions_by_tick = {rec["tick"]: rec.get("positions", {})
                         for rec in world_log}
    seen_claims: set[str] = set()
    seen_found_objects: set[str] = set()
    observations = []
    for ev in flat_events:
        etype = ev["type"]
        claim = ev.get("claim")
        cdef = claims_def.get(claim) if claim else None
        if claim and cdef is None:
            issues.append({"kind": "claim_not_in_scenario", "severity": "warning",
                           "detail": f"event {ev['event_id']} references claim "
                                     f"{claim!r} absent from run_meta scenario "
                                     "snapshot",
                           "event_id": ev["event_id"]})
        features = {
            "opens_session": etype == "session_start",
            "closes_session": etype == "session_end",
            "first_mention_of_claim": bool(claim) and claim not in seen_claims,
            "first_find_of_object": any(o not in seen_found_objects
                                        for o in ev.get("found", [])),
            "has_text": ev.get("text") is not None,
            "is_failed_attempt": etype == "speak_failed",
            "addressee_present": None,
        }
        if claim:
            seen_claims.add(claim)
        for o in ev.get("found", []):
            seen_found_objects.add(o)
        if etype in ("speak", "ask", "confront") and len(ev["actors"]) >= 2:
            addressee = ev["actors"][1]
            pos = positions_by_tick.get(ev["tick"], {})
            if addressee in pos:
                features["addressee_present"] = pos[addressee] == ev["location"]
        extra = {k: v for k, v in ev.items() if k not in CORE_EVENT_FIELDS}
        observations.append({
            "obs_id": ev["event_id"],
            "tick": ev["tick"],
            "time": time_by_tick.get(ev["tick"]),
            "type": etype,
            "actors": list(ev["actors"]),
            "location": ev["location"],
            "claim": claim,
            "proposition": cdef.get("proposition") if cdef else None,
            "stance": cdef.get("stance") if cdef else None,
            "text": ev.get("text"),
            "found": list(ev.get("found", [])),
            "object": ev.get("object"),
            "delivered_to": sorted(delivered_to.get(ev["event_id"], [])),
            "muffled_for": sorted(muffled_for.get(ev["event_id"], [])),
            "narrative_role": ROLE_BY_TYPE.get(etype, "texture"),
            "features": features,
            "fields": extra,
            "raw_ref": f"world_log.json#{ev['event_id']}",
        })
        if etype not in ROLE_BY_TYPE:
            issues.append({"kind": "unknown_event_type", "severity": "warning",
                           "detail": f"event {ev['event_id']} has type {etype!r} "
                                     "absent from design §4.3 table; defaulted "
                                     "to texture",
                           "event_id": ev["event_id"]})

    # ---- knowledge frontier (design §4.4) ----
    event_order = {ev["event_id"]: i for i, ev in enumerate(flat_events)}
    frontier: dict[str, list[dict]] = {cid: [] for cid in character_ids}
    cumulative: dict[str, list[str]] = {cid: [] for cid in character_ids}
    seen_by: dict[str, set[str]] = {cid: set() for cid in character_ids}
    delivered_by_tick: dict[tuple[int, str], list[str]] = {}
    muffled_by_tick: dict[tuple[int, str], list[str]] = {}
    for tick_rec in world_log:
        for d in tick_rec.get("deliveries", []):
            delivered_by_tick[(tick_rec["tick"], d["agent_id"])] = \
                list(d.get("delivered_event_ids", []))
            muffled_by_tick[(tick_rec["tick"], d["agent_id"])] = \
                list(d.get("muffled_event_ids", []))
    own_by_tick: dict[tuple[int, str], list[str]] = {}
    for ev in flat_events:
        for a in ev["actors"]:
            own_by_tick.setdefault((ev["tick"], a), []).append(ev["event_id"])
    for tick_rec in world_log:
        t = tick_rec["tick"]
        for cid in character_ids:
            candidates = own_by_tick.get((t, cid), []) + \
                delivered_by_tick.get((t, cid), [])
            fresh: set[str] = set()
            new_ids = []
            for eid in candidates:
                if eid not in seen_by[cid] and eid not in fresh:
                    fresh.add(eid)
                    new_ids.append(eid)
            new_ids.sort(key=lambda eid: event_order.get(eid, 1 << 30))
            for eid in new_ids:
                seen_by[cid].add(eid)
                cumulative[cid].append(eid)
            frontier[cid].append({
                "tick": t,
                "new_event_ids": new_ids,
                "muffled_event_ids": [e for e in
                                      muffled_by_tick.get((t, cid), [])
                                      if e in known_ids],
                "cumulative_count": len(cumulative[cid]),
            })

    # ---- scenes (design §4.5) ----
    scenes = _build_scenes(world_log, flat_events, issues)

    # ---- write outputs, then verify input hashes (design §7.2) ----
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "observations.json").write_text(_dump(observations),
                                               encoding="utf-8")
    (out_dir / "frontier.json").write_text(
        _dump({cid: frontier[cid] for cid in character_ids}), encoding="utf-8")
    (out_dir / "scenes.json").write_text(_dump(scenes), encoding="utf-8")
    (out_dir / "issues.json").write_text(_dump(issues), encoding="utf-8")

    hashes_after = {f: _sha256(run_dir / f) for f in hashes_before}
    hash_check = "pass" if hashes_after == hashes_before else "FAIL"
    if hash_check == "FAIL":
        issues.append({"kind": "input_hash_changed", "severity": "error",
                       "detail": "an input file changed during observation; "
                                 "this output set is void"})
        (out_dir / "issues.json").write_text(_dump(issues), encoding="utf-8")

    provenance = {
        "observer_version": OBSERVER_VERSION,
        "run_id": meta.get("run_id"),
        "scenario_id": meta.get("scenario_id"),
        "seed": meta.get("seed"),
        "mode": meta.get("mode"),
        "engine_version": meta.get("engine_version"),
        "ticks": len(world_log),
        "total_events": len(flat_events),
        "characters": character_ids,
        "names": names,
        "scenario_source": "run_meta.json embedded snapshot",
        "input_hashes_sha256": hashes_before,
        "input_hash_check_after": hash_check,
        "products": ["observations.json", "frontier.json", "scenes.json",
                     "issues.json", "provenance.json"],
    }
    (out_dir / "provenance.json").write_text(_dump(provenance),
                                             encoding="utf-8")
    if hash_check == "FAIL":
        raise ObserverError(f"{run_dir}: input hash changed during processing")
    return {"run_id": meta.get("run_id"), "events": len(flat_events),
            "scenes": len(scenes), "issues": len(issues),
            "hash_check": hash_check}


def _build_scenes(world_log: list, flat_events: list,
                  issues: list[dict]) -> list[dict]:
    """Scene segmentation per design §4.5 (deterministic rules 1-3)."""
    event_order = {ev["event_id"]: i for i, ev in enumerate(flat_events)}
    by_id = {ev["event_id"]: ev for ev in flat_events}

    # Rule 1: session intervals from session_start/session_end pairs.
    sessions: dict[str, dict] = {}
    for ev in flat_events:
        if ev["type"] == "session_start":
            sessions[ev["session"]] = {
                "session": ev["session"], "start_tick": ev["tick"],
                "end_tick": None, "location": ev["location"],
                "actors": list(ev["actors"]), "open": True,
                "member_ids": [ev["event_id"]],
            }
        elif ev["type"] == "session_end":
            s = sessions.get(ev["session"])
            if s is None:
                issues.append({"kind": "session_end_without_start",
                               "severity": "warning",
                               "detail": f"session_end {ev['event_id']} has no "
                                         f"session_start for {ev['session']!r}",
                               "event_id": ev["event_id"]})
            else:
                s["end_tick"] = ev["tick"]
                s["open"] = False
                s["member_ids"].append(ev["event_id"])
    last_tick = world_log[-1]["tick"] if world_log else 0
    for s in sessions.values():
        if s["end_tick"] is None:
            s["end_tick"] = last_tick

    def session_of(ev) -> str | None:
        best = None
        for sid, s in sessions.items():
            if not (s["start_tick"] <= ev["tick"] <= s["end_tick"]):
                continue
            if ev.get("session_location", ev["location"]) != s["location"] \
                    and ev["location"] != s["location"]:
                continue
            if ev["actors"] and not set(ev["actors"]) & set(s["actors"]):
                continue
            if best is None or s["start_tick"] > sessions[best]["start_tick"]:
                best = sid
        return best

    assigned: dict[str, str] = {}  # event_id -> session id
    for ev in flat_events:
        if ev["type"] in ("session_start", "session_end"):
            continue
        sid = session_of(ev)
        if sid:
            assigned[ev["event_id"]] = sid
            sessions[sid]["member_ids"].append(ev["event_id"])

    scenes: list[dict] = []
    for sid in sorted(sessions, key=lambda k: (sessions[k]["start_tick"], k)):
        s = sessions[sid]
        member_ids = sorted(set(s["member_ids"]),
                            key=lambda eid: event_order[eid])
        participants = sorted({a for eid in member_ids
                               for a in by_id[eid]["actors"]})
        scenes.append({
            "scene_id": None, "kind": "session", "session": sid,
            "start_tick": s["start_tick"], "end_tick": s["end_tick"],
            "location": s["location"], "participants": participants,
            "event_ids": member_ids, "context_event_ids": [],
            "open": s["open"],
        })

    # Rule 2: unassigned plot events group by (tick, location).
    member_of: set[str] = set()
    for sc in scenes:
        member_of.update(sc["event_ids"])
    groups: dict[tuple[int, str], list[str]] = {}
    for ev in flat_events:
        if ev["event_id"] in member_of:
            continue
        if ROLE_BY_TYPE.get(ev["type"], "texture") == "plot":
            groups.setdefault((ev["tick"], ev["location"]),
                              []).append(ev["event_id"])
    loose_scenes = []
    for (tick, loc) in sorted(groups):
        ids = sorted(groups[(tick, loc)], key=lambda eid: event_order[eid])
        loose_scenes.append({
            "scene_id": None, "kind": "loose", "session": None,
            "start_tick": tick, "end_tick": tick, "location": loc,
            "participants": sorted({a for eid in ids
                                    for a in by_id[eid]["actors"]}),
            "event_ids": ids, "context_event_ids": [], "open": False,
        })
        member_of.update(ids)
    scenes.extend(loose_scenes)
    scenes.sort(key=lambda sc: (sc["start_tick"],
                                event_order[sc["event_ids"][0]]))

    # Rule 3: remaining events attach as context to the temporally
    # nearest scene (ties -> the earlier scene); moves by a session
    # participant inside the session interval attach to that session.
    scene_by_session = {sc["session"]: sc for sc in scenes if sc["session"]}
    for ev in flat_events:
        if ev["event_id"] in member_of:
            continue
        target = None
        if ev["type"] == "move" and ev["actors"]:
            for sid, s in sessions.items():
                if ev["actors"][0] in s["actors"] and \
                        s["start_tick"] <= ev["tick"] <= s["end_tick"]:
                    target = scene_by_session[sid]
                    break
        if target is None and scenes:
            target = min(scenes,
                         key=lambda sc: (abs(sc["start_tick"] - ev["tick"]),
                                         sc["start_tick"]))
        if target is not None:
            target["context_event_ids"].append(ev["event_id"])
        member_of.add(ev["event_id"])
    for i, sc in enumerate(scenes, 1):
        sc["scene_id"] = f"SC-{i:03d}"
        sc["context_event_ids"].sort(key=lambda eid: event_order[eid])
    return scenes


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m narrative.observer RUN_DIR OUT_DIR",
              file=sys.stderr)
        return 2
    try:
        result = observe(Path(argv[1]), Path(argv[2]))
    except ObserverError as e:
        print(f"observer stopped: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
