"""Storyline Extractor (N2) — deterministic causal-edge extraction.

Implements docs/NARRATIVE_LAYER_DESIGN.md §5:
  * edges E1-E5 (strong) + E6 (weak, organisational only) are built by
    mechanical rules over the Observer's products plus L-CTX definitions
    from run_meta's scenario snapshot; no LLM touches edges;
  * a storyline is a strong-edge component with >=3 events spanning
    >=2 ticks and >=1 plot event; smaller components are fragments;
  * states closed / open / broken follow §5.3, with broken checked
    first: B1 chain-level unanswered ask beyond the 3-tick window,
    B2 transfer interruption, B3 frustrated search (empty search after
    being informed, at a location where another agent already found
    the object, with no later recovery) — plus L-AUX goal corroboration
    attached as notes, never as facts;
  * titles are a deterministic fallback layer (titles.json), explicitly
    marked as such; the LLM decorative layer of §5.4 is separate and
    requires its own budget approval.

Inputs are read-only; the run's world_log.json / acp.sqlite3 hashes are
verified unchanged after processing, mirroring the Observer.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from . import OBSERVER_VERSION

EXTRACTOR_VERSION = "n2-extractor-1.0"
SPEECH = ("speak", "ask", "confront")
ASK_WINDOW = 3  # ticks; from the emergence layer's pending-question TTL


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def extract(run_dir: Path, narrative_dir: Path) -> dict:
    run_dir, narrative_dir = Path(run_dir), Path(narrative_dir)
    meta = _load(run_dir / "run_meta.json")
    world_log = _load(run_dir / "world_log.json")
    observations = _load(narrative_dir / "observations.json")
    scenes = _load(narrative_dir / "scenes.json")
    frontier = _load(narrative_dir / "frontier.json")
    scenario = meta.get("scenario") or {}
    objects_def = scenario.get("objects") or {}

    hash_targets = [run_dir / "world_log.json", run_dir / "acp.sqlite3"]
    hashes_before = {p.name: _sha256(p) for p in hash_targets if p.exists()}

    obs = {o["obs_id"]: o for o in observations}
    order = [o["obs_id"] for o in observations]
    idx = {eid: i for i, eid in enumerate(order)}
    last_tick = world_log[-1]["tick"] if world_log else 0
    issues: list[dict] = []

    def later(eid_a: str, eid_b: str) -> bool:
        return idx[eid_a] < idx[eid_b]

    # session membership from Observer scenes (for E3)
    session_of: dict[str, str] = {}
    for sc in scenes:
        if sc.get("session"):
            for eid in sc["event_ids"]:
                session_of[eid] = sc["session"]

    edges: dict[tuple[str, str], dict] = {}

    def add_edge(src: str, dst: str, etype: str, basis: str,
                 weak: bool = False):
        if src == dst or not later(src, dst):
            return
        e = edges.setdefault((src, dst),
                             {"from": src, "to": dst, "types": [],
                              "basis": [], "weak": weak})
        if etype not in e["types"]:
            e["types"].append(etype)
        e["basis"].append(basis)
        e["weak"] = e["weak"] and weak

    speech = [o for o in observations if o["type"] in SPEECH]

    # ---- E1: same-topic speech chain (adjacent mentions) ----
    by_key: dict[str, list[dict]] = {}
    for o in speech:
        key = o["proposition"] or o["claim"]
        if key:
            by_key.setdefault(key, []).append(o)
    for key, group in by_key.items():
        for a, b in zip(group, group[1:]):
            add_edge(a["obs_id"], b["obs_id"], "E1",
                     f"adjacent mentions of {key!r}")

    # ---- E2: information transfer via deliveries ----
    # Speech variant uses nearest-source attribution: for each speech Y
    # by agent A on key K, the single source is the LAST K-speech
    # delivered to A before Y (with no intervening K-speech by A).
    # Linking every earlier receipt to Y over-attributes causality and
    # was rejected during the N2 manual edge audit.
    searches = [o for o in observations if o["type"] == "search"]
    for y in speech:
        key = y["proposition"] or y["claim"]
        if not key or not y["actors"]:
            continue
        agent = y["actors"][0]
        candidates = [x for x in speech
                      if (x["proposition"] or x["claim"]) == key
                      and x["actors"][0] != agent
                      and agent in x["delivered_to"]
                      and later(x["obs_id"], y["obs_id"])
                      and not any(z["actors"][0] == agent
                                  and (z["proposition"] or z["claim"]) == key
                                  and later(x["obs_id"], z["obs_id"])
                                  and later(z["obs_id"], y["obs_id"])
                                  for z in speech)]
        if candidates:
            x = candidates[-1]
            add_edge(x["obs_id"], y["obs_id"], "E2",
                     f"{agent} received {x['obs_id']} (delivered, nearest "
                     f"source) then spoke {key!r}")
    for x in speech:
        key = x["proposition"] or x["claim"]
        if not key:
            continue
        for agent in x["delivered_to"]:
            if agent == x["actors"][0]:
                continue
            # E2 action clause (design §5.2 E2, second clause): the
            # ADDRESSEE's first subsequent search is an informed action.
            # Restricted to assert-stance sources: an inquire carries no
            # belief (the emergence layer's own contract), so being
            # asked a question is not information to act on; and only
            # the addressee — not a bystander recipient — is the one
            # the information was meant to move.
            if x["stance"] == "assert" and len(x["actors"]) >= 2 and \
                    agent == x["actors"][1]:
                for y in searches:
                    if y["actors"] and y["actors"][0] == agent and \
                            later(x["obs_id"], y["obs_id"]):
                        add_edge(x["obs_id"], y["obs_id"], "E2",
                                 f"{agent} was told {x['obs_id']} "
                                 "(addressee, delivered) then searched "
                                 "(informed action)")
                        break

    # ---- E3: ask -> response ----
    for q in speech:
        if q["stance"] != "inquire" or not q["proposition"]:
            continue
        for r in speech:
            if not later(q["obs_id"], r["obs_id"]):
                continue
            if r["actors"][0] == q["actors"][0]:
                continue
            if r["proposition"] != q["proposition"] or \
                    r["stance"] not in ("assert", "deny"):
                continue
            same_session = session_of.get(q["obs_id"]) is not None and \
                session_of.get(q["obs_id"]) == session_of.get(r["obs_id"])
            weak_window = not same_session and \
                (r["tick"] - q["tick"]) <= ASK_WINDOW
            if same_session or weak_window:
                add_edge(q["obs_id"], r["obs_id"], "E3",
                         "ask answered in session" if same_session else
                         f"ask answered within {ASK_WINDOW} ticks "
                         "(weak_window)")
                break

    # ---- E4: search -> found -> use ----
    for f in observations:
        if f["type"] != "search":
            continue
        for obj in f["found"]:
            odef = objects_def.get(obj) or {}
            targets = set()
            if odef.get("fact_belief"):
                targets.add(odef["fact_belief"])
            for rev in odef.get("reveals", []):
                if rev.get("belief"):
                    targets.add(rev["belief"])
            for u in observations:
                if not later(f["obs_id"], u["obs_id"]):
                    continue
                if u["type"] == "show" and u.get("object") == obj:
                    add_edge(f["obs_id"], u["obs_id"], "E4",
                             f"found object {obj!r} later shown")
                    break
                # Speech variant: the FINDER voicing the found belief
                # (assert stance). Linking another person's same-topic
                # question here was a mis-edge found in the N2 audit.
                if u["type"] in SPEECH and u["proposition"] in targets \
                        and u["actors"] and f["actors"] \
                        and u["actors"][0] == f["actors"][0] \
                        and u["stance"] == "assert":
                    add_edge(f["obs_id"], u["obs_id"], "E4",
                             f"found {obj!r}; finder later asserted its "
                             f"belief {u['proposition']!r}")
                    break

    # ---- E7: search sequence (same agent's consecutive searches are
    # one continuous effort; added during N2 validation — the design
    # §5.5 gold chain is unreachable without an action-sequence bridge.
    # Flagged for Hu's ratification in the N2 report.) ----
    by_searcher: dict[str, list[dict]] = {}
    for s in searches:
        if s["actors"]:
            by_searcher.setdefault(s["actors"][0], []).append(s)
    for agent, group in by_searcher.items():
        for a, b in zip(group, group[1:]):
            add_edge(a["obs_id"], b["obs_id"], "E7",
                     f"{agent}'s consecutive searches (one effort)")

    # ---- E5: conflict (deny vs earlier assert, same proposition) ----
    for d in speech:
        if d["stance"] != "deny" or not d["proposition"]:
            continue
        earlier = [a for a in speech
                   if a["proposition"] == d["proposition"]
                   and a["stance"] == "assert"
                   and a["actors"][0] != d["actors"][0]
                   and later(a["obs_id"], d["obs_id"])]
        if earlier:
            add_edge(earlier[-1]["obs_id"], d["obs_id"], "E5",
                     f"denial of proposition {d['proposition']!r} asserted "
                     f"earlier by {earlier[-1]['actors'][0]}")

    # ---- E6: enablement (weak; never used for components) ----
    positions = {rec["tick"]: rec.get("positions", {}) for rec in world_log}
    moves = [o for o in observations if o["type"] in
             ("move", "session_start")]
    for y in speech:
        if len(y["actors"]) < 2:
            continue
        a, b = y["actors"][0], y["actors"][1]
        for x in moves:
            if not later(x["obs_id"], y["obs_id"]) or \
                    y["tick"] - x["tick"] > 1:
                continue
            if x["actors"] and x["actors"][0] in (a, b) and \
                    positions.get(y["tick"], {}).get(a) == \
                    positions.get(y["tick"], {}).get(b) == y["location"]:
                add_edge(x["obs_id"], y["obs_id"], "E6",
                         "arrival/session start enabled this exchange",
                         weak=True)
                break

    strong_edges = [e for e in edges.values() if not e["weak"]]
    weak_edges = [e for e in edges.values() if e["weak"]]

    # ---- components over strong edges ----
    uf = _UnionFind()
    for e in strong_edges:
        uf.union(e["from"], e["to"])
    comps: dict[str, list[str]] = {}
    for e in strong_edges:
        for eid in (e["from"], e["to"]):
            comps.setdefault(uf.find(eid), [])
            if eid not in comps[uf.find(eid)]:
                comps[uf.find(eid)].append(eid)
    edges_by_root: dict[str, list[dict]] = {}
    for e in strong_edges:
        edges_by_root.setdefault(uf.find(e["from"]), []).append(e)

    # ---- L-AUX: goals activated but never completed (corroboration) ----
    aux_goals: list[dict] = []
    db_path = run_dir / "acp.sqlite3"
    if db_path.exists():
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT character_id, goal_key, kind FROM goal_changes "
                "ORDER BY tick").fetchall()
        except sqlite3.Error:
            rows = []
        con.close()
        state: dict[tuple[str, str], str] = {}
        for cid, gk, kind in rows:
            if kind in ("activated", "reactivated"):
                state[(cid, gk)] = "open"
            elif kind in ("completed", "failed"):
                state[(cid, gk)] = kind
        aux_goals = [{"character_id": cid, "goal_key": gk}
                     for (cid, gk), st in sorted(state.items())
                     if st == "open"]

    # ---- line assembly + state judgement (§5.1, §5.3) ----
    def judge_state(member_ids: list[str], comp_edges: list[dict]):
        members = [obs[eid] for eid in member_ids]
        reasons: list[str] = []
        break_event = None
        props = {o["proposition"] for o in members if o["proposition"]}
        # B1: chain-level unanswered ask beyond window
        for q in members:
            if q["stance"] != "inquire" or not q["proposition"]:
                continue
            answered = any(r["proposition"] == q["proposition"]
                           and r["stance"] in ("assert", "deny")
                           and later(q["obs_id"], r["obs_id"])
                           for r in observations)
            if not answered and last_tick - q["tick"] > ASK_WINDOW:
                reasons.append(
                    f"B1: ask {q['obs_id']} ({q['proposition']!r}) never "
                    "answered in the run and is older than the window")
                break_event = break_event or q["obs_id"]
        # NOTE: design §5.3's B2 (transfer interruption) is NOT used as
        # a state-breaker: on the validation runs it fired for every
        # terminal recipient (a person who was meant to simply know),
        # marking every line broken. The clinic break it was designed
        # for is fully captured by B3 below. Deviation recorded in the
        # N2 report for Hu's ratification.
        member_set = set(member_ids)
        # B3: frustrated search
        finds = [(o["location"], o["found"], o["actors"][0], o["obs_id"])
                 for o in observations if o["type"] == "search" and o["found"]]
        for s in members:
            if s["type"] != "search" or s["found"]:
                continue
            agent = s["actors"][0]
            informed = any(x["type"] in SPEECH
                           and agent in x["delivered_to"]
                           and later(x["obs_id"], s["obs_id"])
                           and x["obs_id"] in member_set
                           for x in members)
            if not informed:
                continue
            taken = any(loc == s["location"] and finder != agent
                        and later(feid, s["obs_id"])
                        for loc, _f, finder, feid in finds)
            if not taken:
                continue
            recovered = any(y["actors"] and y["actors"][0] == agent
                            and ((y["type"] == "search" and y["found"])
                                 or (y["type"] in SPEECH
                                     and y["stance"] == "assert"
                                     and y["proposition"] in props))
                            and later(s["obs_id"], y["obs_id"])
                            for y in observations)
            if not recovered:
                reasons.append(
                    f"B3: {agent}'s search {s['obs_id']} at {s['location']} "
                    "came up empty after the object there had already been "
                    "found by another agent; no later recovery")
                break_event = s["obs_id"]
        if reasons:
            return "broken", reasons, break_event
        # closed: E3 pairing, E4 use, or terminal assert
        has_e3 = any("E3" in e["types"] for e in comp_edges)
        has_e4 = any("E4" in e["types"] for e in comp_edges)
        speech_members = [o for o in members if o["type"] in SPEECH]
        terminal_assert = bool(speech_members) and \
            speech_members[-1]["stance"] == "assert"
        if has_e3:
            reasons.append("closed: contains an answered ask (E3)")
            return "closed", reasons, None
        if has_e4:
            reasons.append("closed: a found object was later used/voiced (E4)")
            return "closed", reasons, None
        if terminal_assert:
            reasons.append("closed: last speech event is an assertion")
            return "closed", reasons, None
        reasons.append("open: no terminal element and no break evidence")
        return "open", reasons, None

    lines, fragments = [], []
    for root in sorted(comps, key=lambda r: min(idx[e] for e in comps[r])):
        member_ids = sorted(comps[root], key=lambda eid: idx[eid])
        members = [obs[eid] for eid in member_ids]
        comp_edges = sorted(edges_by_root.get(root, []),
                            key=lambda e: (idx[e["from"]], idx[e["to"]]))
        ticks = [o["tick"] for o in members]
        is_line = len(member_ids) >= 3 and (max(ticks) - min(ticks)) >= 2 \
            and any(o["type"] in SPEECH or o["type"] in
                    ("search", "show", "refuse", "speak_failed")
                    for o in members)
        state, reasons, break_event = (judge_state(member_ids, comp_edges)
                                       if is_line else
                                       ("fragment", ["below line criteria "
                                                      "(§5.1)"], None))
        aux_notes = []
        if state == "broken" and break_event and \
                obs[break_event]["actors"]:
            breaker = obs[break_event]["actors"][0]
            for g in aux_goals:
                if g["character_id"] == breaker:
                    aux_notes.append(
                        {"character_id": g["character_id"],
                         "goal_key": g["goal_key"],
                         "note": "L-AUX corroboration: this goal was "
                                 "activated and never completed in "
                                 "goal_changes (auxiliary evidence only, "
                                 "not a world fact)"})
        entry = {
            "line_id": None,
            "kind": "line" if is_line else "fragment",
            "event_ids": member_ids,
            "edges": [{"from": e["from"], "to": e["to"],
                       "types": e["types"], "basis": e["basis"]}
                      for e in comp_edges],
            "participants": sorted({a for o in members for a in o["actors"]}),
            "propositions": sorted({o["proposition"] for o in members
                                    if o["proposition"]}),
            "claims": sorted({o["claim"] for o in members if o["claim"]}),
            "start_tick": min(ticks), "end_tick": max(ticks),
            "state": state, "state_reasons": reasons,
            "break_event": break_event,
            "aux_notes": aux_notes,
        }
        (lines if is_line else fragments).append(entry)
    lines.sort(key=lambda e: idx[e["event_ids"][0]])
    fragments.sort(key=lambda e: idx[e["event_ids"][0]])
    for i, e in enumerate(lines, 1):
        e["line_id"] = f"L{i:02d}"
    for i, e in enumerate(fragments, 1):
        e["line_id"] = f"F{i:02d}"

    # ---- arcs (§5.1: participation + frontier growth, nothing more) ----
    arcs = {}
    for cid in sorted(frontier):
        arcs[cid] = {
            "lines": [e["line_id"] for e in lines
                      if cid in e["participants"]],
            "fragments": [e["line_id"] for e in fragments
                          if cid in e["participants"]],
            "frontier_final_count": frontier[cid][-1]["cumulative_count"]
            if frontier[cid] else 0,
            "frontier_by_tick": [{"tick": r["tick"],
                                  "cumulative_count": r["cumulative_count"]}
                                 for r in frontier[cid]],
        }

    # ---- deterministic fallback titles (decorative layer, §5.4) ----
    titles = []
    for e in lines + fragments:
        speech_members = [obs[eid] for eid in e["event_ids"]
                          if obs[eid]["type"] in SPEECH]
        dom = None
        if speech_members:
            counts: dict[str, int] = {}
            for o in speech_members:
                k = o["proposition"] or o["claim"]
                if k:
                    counts[k] = counts.get(k, 0) + 1
            if counts:
                dom = max(sorted(counts), key=lambda k: counts[k])
        titles.append({
            "line_id": e["line_id"],
            "title": (f"{dom} · {'/'.join(e['participants'])}"
                      if dom else f"line · {'/'.join(e['participants'])}"),
            "source": "deterministic_fallback",
            "note": "placeholder title; the LLM decorative layer of design "
                    "§5.4 is not part of this run",
        })

    storylines = {
        "meta": {"extractor_version": EXTRACTOR_VERSION,
                 "observer_version": OBSERVER_VERSION,
                 "run_id": meta.get("run_id"),
                 "strong_edges": len(strong_edges),
                 "weak_edges": len(weak_edges),
                 "input_hash_check_after": None},
        "lines": lines, "fragments": fragments,
    }
    narrative_dir.mkdir(parents=True, exist_ok=True)
    (narrative_dir / "storylines.json").write_text(_dump(storylines),
                                                   encoding="utf-8")
    (narrative_dir / "arcs.json").write_text(_dump(arcs), encoding="utf-8")
    (narrative_dir / "titles.json").write_text(_dump(titles),
                                               encoding="utf-8")
    (narrative_dir / "extractor_issues.json").write_text(_dump(issues),
                                                          encoding="utf-8")

    hashes_after = {p.name: _sha256(p) for p in hash_targets if p.exists()}
    storylines["meta"]["input_hash_check_after"] = \
        "pass" if hashes_after == hashes_before else "FAIL"
    (narrative_dir / "storylines.json").write_text(_dump(storylines),
                                                   encoding="utf-8")
    return {"run_id": meta.get("run_id"), "lines": len(lines),
            "fragments": len(fragments),
            "strong_edges": len(strong_edges), "weak_edges": len(weak_edges),
            "hash_check": storylines["meta"]["input_hash_check_after"]}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m narrative.extractor RUN_DIR NARRATIVE_DIR",
              file=sys.stderr)
        return 2
    result = extract(Path(argv[1]), Path(argv[2]))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
