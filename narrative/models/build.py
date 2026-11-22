"""Deterministic projection: observations -> NarrativeEvents
(upgrade Phase 1 reference builder; design §1.2).

Rule-only, conservative: a dramatic function is labelled only
when its rule fires; importance is a documented heuristic, not a
fact; knowledge changes come from deliveries and finds only.
"""
from __future__ import annotations

import json
from pathlib import Path

from narrative.models.narrative_event import (CharacterChange,
                                              DramaticFunction,
                                              KnowledgeChange,
                                              NarrativeEvent)

BASE_IMPORTANCE = {"search": 0.6, "confront": 0.6, "speak": 0.3,
                   "ask": 0.35, "show": 0.5, "refuse": 0.5,
                   "session_start": 0.1, "session_end": 0.1,
                   "move": 0.05, "observe": 0.05, "ambient": 0.05,
                   "listen": 0.05, "wait": 0.02, "work": 0.05}
SPEECH = ("speak", "ask", "confront")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_narrative_events(run_dir: Path, narrative_dir: Path
                           ) -> list[NarrativeEvent]:
    observations = _load(narrative_dir / "observations.json")
    storylines = _load(narrative_dir / "storylines.json")
    meta = _load(run_dir / "run_meta.json")
    scenario = meta.get("scenario") or {}
    claims = scenario.get("claims") or {}
    object_facts: dict[str, dict] = {}
    for obj_id, od in (scenario.get("objects") or {}).items():
        object_facts[obj_id] = {
            "direct": [od["fact_belief"]] if od.get("fact_belief")
                      else [],
            "hearsay": [r["belief"] for r in od.get("reveals", [])]}
    line_members: set[str] = set()
    for line in storylines.get("lines", []):
        line_members |= set(line.get("event_ids", []))

    known: dict[str, set[str]] = {}
    fact_first_event: dict[str, str] = {}
    asserted_props: dict[str, str] = {}
    denied_props: set[str] = set()
    confronted_props: set[str] = set()
    events: list[NarrativeEvent] = []
    for obs in observations:
        eid = obs["obs_id"]
        tick = obs["tick"]
        actors = obs.get("actors") or []
        prop = obs.get("proposition")
        features = obs.get("features") or {}
        functions: list[DramaticFunction] = []
        knowledge: list[KnowledgeChange] = []
        characters: list[CharacterChange] = []

        def learn(char: str, fact: str, via: str) -> str:
            facts = known.setdefault(char, set())
            change = "reinforced" if fact in facts else "learned"
            facts.add(fact)
            fact_first_event.setdefault(fact, eid)
            return change

        if obs["type"] == "search":
            finder = actors[0] if actors else ""
            for obj in obs.get("found") or []:
                if features.get("first_find_of_object"):
                    functions.append(DramaticFunction.DISCOVERY)
                for fact in (object_facts.get(obj, {}).get(
                        "direct", []) + object_facts.get(
                        obj, {}).get("hearsay", [])):
                    change = learn(finder, fact, "find")
                    knowledge.append(KnowledgeChange(
                        character=finder, fact=fact, change=change,
                        via="find", source_event_id=eid))
                    if change == "learned":
                        characters.append(CharacterChange(
                            character=finder, dimension="belief",
                            description=f"learned {fact} by finding "
                                        f"{obj}",
                            evidence_event_ids=[eid]))
        if obs["type"] in SPEECH and prop:
            speaker = actors[0] if actors else ""
            cdef = claims.get(obs.get("claim") or "", {})
            if obs.get("stance") == "assert":
                if cdef.get("speaker_kind") == "experienced" and \
                        speaker:
                    change = learn(speaker, prop, "experienced")
                    knowledge.append(KnowledgeChange(
                        character=speaker, fact=prop, change=change,
                        via="experienced", source_event_id=eid))
                new_audience = []
                for char in obs.get("delivered_to") or []:
                    if char == speaker:
                        continue
                    change = learn(char, prop, "delivery")
                    knowledge.append(KnowledgeChange(
                        character=char, fact=prop, change=change,
                        via="delivery", source_event_id=eid))
                    if change == "learned":
                        new_audience.append(char)
                if new_audience and \
                        fact_first_event.get(prop) == eid:
                    pass  # speaker's own first disclosure handled
                    # below via asserted_props
                if prop not in asserted_props:
                    asserted_props[prop] = eid
                    if new_audience:
                        functions.append(
                            DramaticFunction.REVELATION)
            if obs.get("stance") == "deny":
                denied_props.add(prop)
            if obs["type"] == "confront":
                functions.append(DramaticFunction.CONFRONTATION)
                confronted_props.add(prop)
                if prop in asserted_props and \
                        asserted_props[prop] != eid:
                    functions.append(DramaticFunction.ESCALATION)
            elif obs.get("stance") == "assert" and \
                    prop in asserted_props and \
                    asserted_props[prop] != eid and \
                    (prop in denied_props or
                     prop in confronted_props):
                # escalation requires contest: a mere relay of an
                # already-asserted proposition is repetition, not
                # escalation (Phase 2 correction; see
                # docs/NOVEL_PHASE1_REPORT.md known roughness)
                functions.append(DramaticFunction.ESCALATION)
            if obs["type"] == "ask":
                functions.append(DramaticFunction.SETUP)
        if features.get("opens_session"):
            functions.append(DramaticFunction.INTRODUCTION)

        importance = BASE_IMPORTANCE.get(obs["type"], 0.1)
        if eid in line_members:
            importance += 0.2
        if any(k.change == "learned" for k in knowledge):
            importance += 0.2
        if DramaticFunction.DISCOVERY in functions or \
                DramaticFunction.REVELATION in functions:
            importance += 0.1
        importance = round(min(1.0, importance), 3)

        text = (obs.get("text") or "").strip()
        summary = text[:120] if text else \
            f"{obs['type']} @ {obs.get('location') or ''}"
        events.append(NarrativeEvent(
            narrative_event_id=f"NE-{eid}",
            source_event_ids=[eid], tick=tick,
            event_summary=summary, summary_source="source_text",
            participants=actors, location=obs.get("location") or "",
            event_type=obs["type"], dramatic_function=functions,
            narrative_importance=importance,
            knowledge_changes=knowledge,
            character_changes=characters,
            interpretation_confidence=1.0 if functions else None))
    return events


def write_narrative_events(run_dir: Path, narrative_dir: Path,
                           out_dir: Path) -> Path:
    events = build_narrative_events(run_dir, narrative_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "narrative_events.json"
    path.write_text(json.dumps(
        [e.model_dump(mode="json") for e in events],
        ensure_ascii=False, indent=1), encoding="utf-8")
    return path
