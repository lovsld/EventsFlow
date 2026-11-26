"""Unified character-state READ VIEW (Phase 2, task book 4.1).

This is an adapter, not a new state system: every field is
aggregated from an existing producer and tagged with its
provenance tier --
  fact        recorded by the simulation / derived from
              committed events (goals, learned facts, arcs)
  inference   evidence-based psychological inference
              (psychology.py emotions)
  hypothesis  unconfirmed motivational inference
              (psychology.py MOTIVATED_BY)
Nothing here writes back to any producer, and inferred content
is never relabelled as fact.
"""
from __future__ import annotations

import json
from pathlib import Path


def _load(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _names(run_dir: Path, character_ids: list[str]) -> dict:
    from narrative.observer import resolve_names
    issues: list[dict] = []
    raw = resolve_names(run_dir.parent.parent, character_ids,
                        issues)
    return {cid: (v.get("name") if isinstance(v, dict) else v)
            for cid, v in raw.items()}


def build_character_states(run_dir: Path,
                           narrative_dir: Path) -> dict:
    upgrade = narrative_dir / "upgrade"
    arcs = {a["character_id"]: a for a in
            _load(upgrade / "character_arcs.json",
                  {"arcs": []})["arcs"]}
    views = _load(upgrade / "story_views.json",
                  {"threads": [], "conflicts": []})
    psy_path = narrative_dir / "phase1" / "psychology.json"
    if psy_path.exists():
        psy = _load(psy_path)
    else:
        from narrative.psychology import build_psychology
        psy = build_psychology(narrative_dir)
    char_ids = sorted(p.name for p in
                      (run_dir / "characters").iterdir()) \
        if (run_dir / "characters").exists() else []
    names = _names(run_dir, char_ids)

    emotions_by_char: dict[str, list[dict]] = {}
    for e in psy.get("emotions", []):
        emotions_by_char.setdefault(e["character_id"], []).append(e)
    mot_by_char: dict[str, list[dict]] = {}
    for m in psy.get("motivated_by", []):
        mot_by_char.setdefault(m["character_id"], []).append(m)

    universe: set[str] = set()
    for a in arcs.values():
        universe |= set(a.get("current_state", {})
                        .get("known_facts", []))

    characters: dict[str, dict] = {}
    char_dirs = sorted((run_dir / "characters").iterdir()) \
        if (run_dir / "characters").exists() else []
    for cdir in char_dirs:
        cid = cdir.name
        state = _load(cdir / "state.json", {})
        arc = arcs.get(cid, {})
        cur = arc.get("current_state", {})
        changes = {c["fact"]: c for c in
                   arc.get("belief_changes", [])}
        initial = set(arc.get("initial_beliefs", []))
        known = []
        for fact in cur.get("known_facts", []):
            ch = changes.get(fact)
            known.append({
                "fact": fact,
                "learned_at_event": ch["event_id"] if ch else None,
                "learned_at_tick": ch["tick"] if ch else None,
                "via": ch["via"] if ch else
                       ("seed" if fact in initial else "unknown")})
        beliefs = [{
            "key": b.get("key"), "confidence": b.get("confidence"),
            "kind": b.get("kind"),
            "held_as": "fact" if (b.get("confidence") or 0) >= 0.9
                       and b.get("kind") == "experienced"
                       else "belief",
            "evidence_sources": [e.get("source_event_id")
                                 for e in b.get("evidence", [])]}
            for b in state.get("beliefs", [])]
        emos = sorted(emotions_by_char.get(cid, []),
                      key=lambda e: e.get("tick", 0))
        current_emotion = next(
            (e for e in reversed(emos)
             if e["emotion"] != "unknown"), emos[-1] if emos else None)
        goals = []
        seen_goals = set()
        for g in state.get("goals", []):
            if g.get("key") in seen_goals:
                continue
            seen_goals.add(g.get("key"))
            goals.append({"key": g.get("key"), "desc": g.get("desc"),
                          "status": g.get("status"),
                          "weight": g.get("weight"),
                          "source": "character_config"})
        for key, aux in (cur.get("goals_aux") or {}).items():
            goals.append({"key": key, "status": aux.get("status"),
                          "progress": aux.get("progress"),
                          "source": "goal_engine_aux"})
        concerns = [{"kind": "goal_not_progressing",
                     "goal": g["key"], "status": g["status"]}
                    for g in goals
                    if g.get("status") in ("failed", "blocked")]
        rels = [{"conflict_id": c["conflict_id"],
                 "type": c.get("conflict_type"),
                 "core_issue": c.get("core_issue"),
                 "with": [p for p in c.get("participants", [])
                          if p != cid]}
                for c in views.get("conflicts", [])
                if cid in c.get("participants", [])]
        characters[cid] = {
            "character_id": cid, "name": names.get(cid, cid),
            "goals": goals,
            "knowledge": {
                "tier": "fact",
                "known_facts": known,
                "beliefs": beliefs,
                "not_known_key_facts": sorted(
                    universe - set(cur.get("known_facts", [])))},
            "emotions": {
                "tier": "inference",
                "current": current_emotion,
                "trajectory": emos},
            "motivations": {
                "tier": "hypothesis",
                "items": mot_by_char.get(cid, [])},
            "internal_conflict": arc.get("internal_conflict"),
            "concerns": concerns,
            "relationships": {
                "tier": "fact",
                "conflicts": rels,
                "state": state.get("relationships", {})},
            "arc": {
                "tier": "fact",
                "arc_type": arc.get("character_arc_type"),
                "arc_status": arc.get("arc_status"),
                "turning_points": arc.get("turning_points", [])}}
    return {"run_id": run_dir.name,
            "tiers": {"fact": "recorded by the simulation or "
                              "derived from committed events",
                      "inference": "evidence-based psychological "
                                   "inference (psychology.py)",
                      "hypothesis": "unconfirmed motivational "
                                    "inference (MOTIVATED_BY)"},
            "characters": characters}


def knowledge_at(view: dict, tick: int) -> list[str]:
    """Facts the character knew by the end of `tick`
    (seed/initial beliefs plus belief changes up to tick)."""
    out = []
    for k in view["knowledge"]["known_facts"]:
        if k["via"] == "seed" or k["learned_at_tick"] is None \
                or k["learned_at_tick"] <= tick:
            out.append(k["fact"])
    return out


def write_character_states(run_dir: Path, narrative_dir: Path,
                           out_dir: Path | None = None) -> Path:
    data = build_character_states(run_dir, narrative_dir)
    out = out_dir or (narrative_dir / "phase2")
    out.mkdir(parents=True, exist_ok=True)
    path = out / "character_states.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path
