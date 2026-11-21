"""Scene Runtime (task book §10.3): the per-tick conductor.

For each committed tick record it updates, in order: the Story
Engine's books, the scene registry, the propagation ledger, and the
consistency checks; then it runs the propose pipeline and returns
any validated candidates as PENDING world events. Pending events
are the injection contract: they become real only when the World
Engine commits them at a later tick (see the Phase 3 report for
the minimal world-side queue patch this requires — not applied).
"""
from __future__ import annotations

import json
from pathlib import Path

from v03.scenes.scene_manager import (ConsistencyValidator,
                                      PropagationTracker, SceneManager)
from v03.story.eventgen import to_world_event
from v03.story.models import StoryConfig
from v03.story.story_engine import StoryEngine


def replay_runtime(run_dir: str | Path, config: StoryConfig,
                   characters_root: str | Path | None = None
                   ) -> SceneRuntime:
    """Feed a finished run's committed ticks through the runtime
    (the same processing a live tick source would get)."""
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "run_meta.json")
                      .read_text(encoding="utf-8"))
    scenario = meta.get("scenario") or {}
    claims = scenario.get("claims") or {}
    object_facts: dict[str, dict] = {}
    for obj_id, od in (scenario.get("objects") or {}).items():
        object_facts[obj_id] = {
            "direct": [od["fact_belief"]] if od.get("fact_belief")
                      else [],
            "hearsay": [r["belief"] for r in od.get("reveals", [])]}
    engine = StoryEngine(config, characters_root)
    engine.set_object_state(
        {oid: od.get("holder") for oid, od in
         (scenario.get("objects") or {}).items() if od.get("holder")},
        {oid: od.get("location") for oid, od in
         (scenario.get("objects") or {}).items() if od.get("location")})
    engine._scenario_locations = list(scenario.get("locations") or [])
    basis_free = {cid for cid, cdef in claims.items()
                  if cdef.get("basis_required") is False}
    runtime = SceneRuntime(engine, basis_free_claims=basis_free)
    world_log = json.loads((run_dir / "world_log.json")
                           .read_text(encoding="utf-8"))
    last_tick = 0
    for tick_record in world_log:
        last_tick = tick_record["tick"]
        for event in tick_record.get("events", []):
            claim = event.get("claim")
            if claim and claim in claims:
                cdef = claims[claim]
                if "proposition" not in event:
                    event["proposition"] = cdef.get("proposition")
                    event["stance"] = cdef.get("stance")
                if cdef.get("speaker_kind") == "experienced":
                    event["_direct"] = True
        runtime.process_tick(tick_record, object_facts)
    runtime.finalize(last_tick)
    return runtime


class SceneRuntime:
    def __init__(self, engine: StoryEngine,
                 basis_free_claims: set[str] | None = None):
        self.engine = engine
        self.scenes = SceneManager(threads=engine.state.threads)
        self.propagation = PropagationTracker(
            engine.config.initial_knowledge,
            basis_free_claims=basis_free_claims)
        self.validator = ConsistencyValidator()
        self.issues = []
        self.pending_events: list[dict] = []

    def process_tick(self, tick_record: dict,
                     object_facts: dict[str, dict]) -> dict:
        delivered: dict[str, list[str]] = {}
        for d in tick_record.get("deliveries", []):
            for eid in d.get("delivered_event_ids", []):
                delivered.setdefault(eid, []).append(d["agent_id"])
        self.engine.observe_tick(tick_record, object_facts)
        scene_result = self.scenes.process_tick(tick_record)
        scene_by_location = {loc: s.scene_id
                             for loc, s in self.scenes.active.items()}
        # scenes that closed this tick keep their ids for attribution
        for s in self.scenes.scenes.values():
            if s.end_tick is not None and \
                    s.end_tick >= tick_record["tick"] - 1:
                scene_by_location.setdefault(s.location_id, s.scene_id)
        self.propagation.process_tick(tick_record, scene_by_location,
                                      delivered, object_facts)
        tick_issues = self.validator.check_tick(tick_record)
        self.issues += tick_issues
        proposals = self.engine.propose(self.engine.snapshot())
        pending = [to_world_event(c, "PENDING")
                   for c in proposals]
        self.pending_events += pending
        return {"tick": tick_record["tick"], **scene_result,
                "proposals": [c.candidate_id for c in proposals],
                "pending_events": pending,
                "issues": [i.model_dump() for i in tick_issues]}

    def finalize(self, last_tick: int) -> dict:
        self.scenes.finalize(last_tick)
        return {
            "scenes": [s.model_dump()
                       for s in self.scenes.scenes.values()],
            "propagations": [p.model_dump()
                             for p in self.propagation.propagations],
            "propagation_issues": [
                i.model_dump() for i in self.propagation.issues],
            "consistency_issues": [i.model_dump() for i in self.issues],
            "pending_events": self.pending_events}
