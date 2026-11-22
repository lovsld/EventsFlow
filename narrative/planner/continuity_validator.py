"""Continuity validation over the plan (upgrade Phase 5).

Checks, all deterministic:
  1. chapter event order is globally non-decreasing in tick;
  2. no character asserts a fact before learning it (knowledge
     frontier from the NarrativeEvent layer + initial knowledge);
  3. high-importance events (>=0.8) are covered by exactly one
     scene — never dropped, never duplicated;
  4. the story outline never presents an open thread as
     resolved.
"""
from __future__ import annotations

import json
from pathlib import Path


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_plan(run_dir: Path, narrative_dir: Path, plan: dict,
                  config_path: Path | None) -> dict:
    upgrade = narrative_dir / "upgrade"
    events = _load(upgrade / "narrative_events.json")
    observations = _load(narrative_dir / "observations.json")
    ne_by_id = {e["narrative_event_id"]: e for e in events}
    obs_by_id = {o["obs_id"]: o for o in observations}
    errors: list[str] = []
    warnings: list[str] = []

    # 1. global order across chapters
    last_tick = -1
    for ch in plan.get("chapters", []):
        for n in ch.get("included_events", []):
            tick = ne_by_id[n]["tick"]
            if tick < last_tick:
                errors.append(f"{ch['chapter_id']}: event {n} at "
                              f"tick {tick} follows tick {last_tick}")
            last_tick = max(last_tick, tick)

    # 2. knowledge frontier (config + seed beliefs from the
    # run's own character state files)
    from narrative.initial_knowledge import seed_initial_knowledge
    initial: dict[str, set[str]] = {
        c: set(f) for c, f in
        seed_initial_knowledge(run_dir).items()}
    if config_path is not None and config_path.exists():
        cfg = _load(config_path)
        for c, f in (cfg.get("initial_knowledge") or {}).items():
            initial.setdefault(c, set()).update(f)
    known: dict[str, set[str]] = {c: set(f)
                                  for c, f in initial.items()}
    claims = (_load(run_dir / "run_meta.json").get("scenario")
              or {}).get("claims") or {}
    for e in events:
        obs = obs_by_id[e["source_event_ids"][0]]
        if obs.get("type") in ("speak", "confront") and \
                obs.get("stance") == "assert" and \
                obs.get("proposition"):
            speaker = (obs.get("actors") or [""])[0]
            cdef = claims.get(obs.get("claim") or "", {})
            if cdef.get("basis_required") and \
                    obs["proposition"] not in known.get(speaker,
                                                        set()):
                errors.append(
                    f"{e['narrative_event_id']}: {speaker} asserts "
                    f"{obs['proposition']} before learning it")
        for k in e.get("knowledge_changes", []):
            if k["change"] == "learned":
                known.setdefault(k["character"], set()).add(
                    k["fact"])

    # 3. scene coverage of high-importance events
    covered: dict[str, int] = {}
    for sc in plan.get("scenes", []):
        for n in sc.get("event_ids", []):
            covered[n] = covered.get(n, 0) + 1
    for e in events:
        if e["narrative_importance"] >= 0.8:
            count = covered.get(e["narrative_event_id"], 0)
            if count == 0:
                errors.append(f"{e['narrative_event_id']}: "
                              f"high-importance event in no scene")
            elif count > 1:
                errors.append(f"{e['narrative_event_id']}: in "
                              f"{count} scenes")

    # 4. no false resolution in the outline
    story = plan.get("story", {})
    views = _load(upgrade / "story_views.json")
    open_titles = [t["title"] for t in views.get("threads", [])
                   if t["status"] != "resolved" and t["title"]]
    ending = story.get("ending_state", "")
    for title in open_titles:
        if title and f"「{title}」状态 resolved" in ending:
            errors.append(f"outline marks open thread {title} "
                          f"resolved")
    return {"errors": errors, "warnings": warnings,
            "checked_events": len(events),
            "scenes": len(plan.get("scenes", [])),
            "chapters": len(plan.get("chapters", []))}
