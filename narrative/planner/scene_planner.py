"""Dramatic scene segmentation (upgrade Phase 5; design §5).

Scenes are NOT cut by tick or location alone. A new scene starts
when the dramatic unit changes: the location changes, the cast
present at that location changes (from the world log's per-tick
positions), or the action goes quiet for a long stretch. Minor
scenes (<2 plot events) merge into their predecessor. Ambient /
movement events are interstitial material, never scenes.
"""
from __future__ import annotations

import json
from pathlib import Path

from narrative.models.story import SceneOutline

GAP_TICKS = 3


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _casts(run_dir: Path) -> dict[int, dict[str, frozenset]]:
    wl = _load(run_dir / "world_log.json")
    out: dict[int, dict[str, frozenset]] = {}
    for record in wl:
        positions = record.get("positions") or {}
        by_loc: dict[str, set[str]] = {}
        for char, loc in positions.items():
            by_loc.setdefault(loc, set()).add(char)
        out[record["tick"]] = {loc: frozenset(chars)
                               for loc, chars in by_loc.items()}
    return out


def segment_scenes(run_dir: Path, narrative_dir: Path) -> list[dict]:
    observations = _load(narrative_dir / "observations.json")
    upgrade = narrative_dir / "upgrade"
    events = _load(upgrade / "narrative_events.json")
    ne_by_id = {e["narrative_event_id"]: e for e in events}
    casts = _casts(run_dir)
    plot = [o for o in observations
            if o.get("narrative_role") == "plot"]
    segments: list[dict] = []
    current: dict | None = None
    for o in plot:
        tick = o["tick"]
        loc = o.get("location") or ""
        cast = casts.get(tick, {}).get(loc, frozenset(
            o.get("actors") or []))
        ne = ne_by_id.get(f"NE-{o['obs_id']}", {})
        # a major turning event opens a new dramatic unit when
        # the current scene has already accumulated substance
        turning_break = current is not None and \
            len(current["event_ids"]) >= 6 and \
            ne.get("narrative_importance", 0.0) >= 0.8 and \
            bool({"discovery", "revelation", "confrontation"} &
                 set(ne.get("dramatic_function", [])))
        start_new = current is None or loc != current["location"] \
            or cast != current["cast"] or \
            tick - current["last_tick"] > GAP_TICKS or turning_break
        if start_new:
            current = {"location": loc, "cast": cast,
                       "start": tick, "last_tick": tick,
                       "event_ids": []}
            segments.append(current)
        current["event_ids"].append(o["obs_id"])
        current["last_tick"] = tick
    # merge micro-scenes into their predecessor
    merged: list[dict] = []
    for seg in segments:
        if merged and len(seg["event_ids"]) < 2:
            prev = merged[-1]
            prev["event_ids"].extend(seg["event_ids"])
            prev["last_tick"] = seg["last_tick"]
        else:
            merged.append(seg)
    for i, seg in enumerate(merged, 1):
        seg["scene_id"] = f"SC{i:02d}"
        seg["ne_ids"] = [f"NE-{e}" for e in seg["event_ids"]]
        seg["importance"] = max(
            (ne_by_id[n]["narrative_importance"]
             for n in seg["ne_ids"] if n in ne_by_id), default=0.0)
    return merged


def build_scene_outlines(run_dir: Path, narrative_dir: Path,
                         views: dict) -> list[dict]:
    segments = segment_scenes(run_dir, narrative_dir)
    upgrade = narrative_dir / "upgrade"
    events = _load(upgrade / "narrative_events.json")
    ne_by_id = {e["narrative_event_id"]: e for e in events}
    threads = views.get("threads", [])
    conflicts = {c["conflict_id"]: c
                 for c in views.get("conflicts", [])}
    outlines: list[dict] = []
    for seg in segments:
        nes = [ne_by_id[n] for n in seg["ne_ids"] if n in ne_by_id]
        if not nes:
            continue
        # dominant thread: most related events inside the scene
        best_thread, best_overlap = None, -1
        for th in threads:
            overlap = len(set(th.get("related_events", [])) &
                          set(seg["ne_ids"]))
            if overlap > best_overlap:
                best_thread, best_overlap = th, overlap
        # viewpoint: most knowledge change, then most actions
        learn_count: dict[str, int] = {}
        act_count: dict[str, int] = {}
        for e in nes:
            for k in e.get("knowledge_changes", []):
                learn_count[k["character"]] = \
                    learn_count.get(k["character"], 0) + 1
            for p in e.get("participants", []):
                act_count[p] = act_count.get(p, 0) + 1
        viewpoint = ""
        if learn_count:
            viewpoint = max(learn_count, key=learn_count.get)
        elif act_count:
            viewpoint = max(act_count, key=act_count.get)
        turning = max(nes, key=lambda e: e["narrative_importance"])
        participants = sorted({p for e in nes
                               for p in e.get("participants", [])})
        opposition = ""
        if best_thread:
            for cid in best_thread.get("related_conflicts", []):
                if cid in conflicts:
                    opposition = conflicts[cid].get(
                        "core_issue", "")
                    break
        outline = SceneOutline(
            scene_id=seg["scene_id"], location=seg["location"],
            time_range=(seg["start"], seg["last_tick"]),
            participants=participants,
            viewpoint_character=viewpoint,
            scene_goal=(best_thread or {}).get(
                "central_question", ""),
            opposition=opposition,
            stakes=(best_thread or {}).get("stakes", ""),
            conflict=(best_thread or {}).get("thread_id", ""),
            turning_point=turning["narrative_event_id"]
            if turning["narrative_importance"] >= 0.5 else None,
            outcome=nes[-1]["event_summary"],
            next_scene_hook="",
            event_ids=seg["ne_ids"])
        outlines.append(outline.model_dump(mode="json"))
    # hooks: the next scene's dominant unresolved question
    for i, outline in enumerate(outlines[:-1]):
        nxt = outlines[i + 1]
        outline["next_scene_hook"] = nxt["scene_goal"] or \
            nxt["outcome"]
    return outlines


# ------------------------------------------------------------------
# Phase 2 (task book 4.3): enrich scene outlines with character
# state. The planner now reads the unified character-state view:
# per participant it records the goals they bring in, what they
# know / must not use at scene entry, their entering emotion,
# and their conflicts with co-participants; per scene it records
# information gaps, the state changes the scene's events are
# expected to produce, and flags scenes with no expected change
# (transition-scene candidates). All values come from the state
# view and the incremental update records -- nothing is invented
# here, and expectations never overwrite what actually happens.
# ------------------------------------------------------------------

def enrich_scenes_with_states(scenes: list[dict], states: dict,
                              updates: list[dict],
                              narrative_dir=None) -> list[dict]:
    from narrative.character_state import knowledge_at
    chars = states.get("characters", {})
    # Phase 3: repetition directives + information density.
    # Directives come from semantic merge groups: the first
    # occurrence of a repeated line/fact-relay is kept in full;
    # later occurrences are compressed UNLESS they teach
    # someone something, change stance, or are a confrontation
    # (dramatic repetition is kept, mechanical repetition is
    # not). Density grades a scene by knowledge changes,
    # distinct propositions and confrontation events.
    group_of: dict[str, dict] = {}
    ne_by_id: dict[str, dict] = {}
    if narrative_dir is not None:
        import json as _json
        from pathlib import Path as _P
        nd = _P(narrative_dir)
        sem = nd / "upgrade" / "semantic.json"
        if sem.exists():
            for g in _json.loads(sem.read_text(
                    encoding="utf-8")).get("groups", []):
                for m in g["member_ne_ids"]:
                    group_of[m] = g
        ev = nd / "upgrade" / "narrative_events.json"
        if ev.exists():
            ne_by_id = {e["narrative_event_id"]: e
                        for e in _json.loads(ev.read_text(
                            encoding="utf-8"))}
    seen_groups: set[str] = set()
    for scene in scenes:
        start, end = (scene.get("time_range") or [0, 0])[:2]
        parts = {}
        for cid in scene.get("participants", []):
            view = chars.get(cid)
            if view is None:
                continue
            entering = set(knowledge_at(view, start - 1))
            must_not = [k["fact"] for k in
                        view["knowledge"]["known_facts"]
                        if k["fact"] not in entering]
            emo = None
            for rec in view["emotions"]["trajectory"]:
                if (rec.get("tick") or 0) < start:
                    emo = rec
            parts[cid] = {
                "goals_active": [g["key"] for g in view["goals"]
                                 if g.get("status") in
                                 ("active", None)],
                "knows_at_entry": sorted(entering),
                "must_not_use_at_entry": sorted(must_not),
                "emotion_entering": emo,
                "conflicts_with": [
                    c for c in
                    view["relationships"]["conflicts"]
                    if any(p in scene.get("participants", [])
                           for p in c["with"])]}
        gaps = []
        cids = [c for c in scene.get("participants", [])
                if c in parts]
        for a in cids:
            for b in cids:
                if a >= b:
                    continue
                a_only = sorted(set(parts[a]["knows_at_entry"])
                                - set(parts[b]["knows_at_entry"]))
                b_only = sorted(set(parts[b]["knows_at_entry"])
                                - set(parts[a]["knows_at_entry"]))
                if a_only or b_only:
                    gaps.append({"pair": [a, b],
                                 f"{a}_only": a_only,
                                 f"{b}_only": b_only})
        expected = [{"character_id": u["character_id"],
                     "field": u["field"], "tier": u["tier"],
                     "after": u["after"],
                     "event_id": u["event_id"]}
                    for u in updates
                    if u.get("tick") is not None
                    and start <= u["tick"] <= end
                    and u["character_id"] in
                    scene.get("participants", [])]
        scene["character_state"] = {
            "participants": parts,
            "information_gaps": gaps,
            "expected_state_changes": expected,
            "no_expected_change": not expected}
        # voice summary rides inside the participant block
        for cid, p in parts.items():
            view = chars.get(cid) or {}
            p["voice"] = view.get("voice")
        # repetition directives + density (Phase 3)
        directives: dict[str, dict] = {}
        learn_events = {e["event_id"] for e in expected
                        if e["field"] == "knowledge"}
        density_score = 2 * len(learn_events)
        props: set[str] = set()
        confronts = 0
        for ne_id in scene.get("event_ids", []):
            ne = ne_by_id.get(ne_id, {})
            if ne.get("proposition"):
                props.add(ne["proposition"])
            if ne.get("event_type") == "confront" or \
                    "confrontation" in (ne.get(
                        "dramatic_function") or []):
                confronts += 1
            g = group_of.get(ne_id)
            if g is None:
                continue
            # reader-level key: a fact relayed again with the
            # same stance teaches a CHARACTER something new,
            # but the READER already knows it -- full rendering
            # is kept only for the first presentation and for
            # confrontations; teaching repeats compress into a
            # learning beat (the learning is shown, the line is
            # not re-quoted).
            key = (g.get("proposition") or g.get("text"),
                   g.get("stance") or g.get("kind"))
            if key not in seen_groups:
                directives[ne_id] = {
                    "action": "keep",
                    "reason": "first presentation to the reader"}
                seen_groups.add(key)
            elif ne.get("event_type") == "confront":
                directives[ne_id] = {
                    "action": "keep",
                    "reason": "confrontation (dramatic repeat)"}
            else:
                directives[ne_id] = {
                    "action": "compress",
                    "reason": "reader already knows this "
                              "proposition/stance" +
                              ("; render as a learning beat "
                               "(a character learns it here)"
                               if ne.get("knowledge_changes")
                               else "; no new information")}
        density_score += len(props) + 2 * confronts
        scene["repetition_directives"] = directives
        scene["density"] = {
            "score": density_score,
            "grade": "high" if density_score >= 5 else
                     ("mid" if density_score >= 2 else "low"),
            "components": {"knowledge_changes":
                           len(learn_events),
                           "propositions": len(props),
                           "confrontations": confronts}}
    return scenes
