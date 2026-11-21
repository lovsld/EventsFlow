"""Story Engine facade (task book §2.1 B, migration plan §3).

Phase 1 implements the OBSERVE level of the tick-boundary hook:
the engine reads committed tick records and maintains its own
bookkeeping (threads, conflicts, stagnation signals). It has no
write path into the world, into character state, or into history —
structurally, this module imports nothing from v02's decision or
world code and exposes no mutation API beyond its own state.

Replay: a finished run's world_log can be fed through the same
observe path (used for validation and for Phase 6 A/B comparison).
"""
from __future__ import annotations

import json
from pathlib import Path

from v03.story.architect import StoryArchitect
from v03.story.conflict_engine import ConflictEngine
from v03.story.eventgen import (CausalValidator, DramaticEvaluator,
                                EventGenerator, WorldRuleValidator,
                                WorldSnapshot)
from v03.story.models import EventCandidate, StoryConfig, StoryState
from v03.story.thread_manager import PLOT_TYPES, ThreadManager


class StoryEngine:
    def __init__(self, config: StoryConfig,
                 characters_root: str | Path | None = None):
        self.config = config
        self.state: StoryState = StoryArchitect().build(
            config, characters_root)
        self.threads = ThreadManager(
            self.state, dormant_after=config.dormant_after_ticks)
        self.conflicts = ConflictEngine(self.state)
        for initial in config.initial_conflicts:
            self.conflicts.add_declared(initial, tick=0)
        self.conflicts.detect(tick=0)
        self._developed_last_tick: bool = False
        # Phase 2 pipeline state
        self.generator = EventGenerator(self.state)
        self.known_event_ids: set[str] = set()
        self.seen_fingerprints: set[str] = set()
        self.last_proposal_tick: int = -10 ** 9
        self.object_holders: dict[str, str] = {}
        self.object_locations: dict[str, str] = {}
        self.candidates_log: list[EventCandidate] = []

    def set_object_state(self, holders: dict[str, str],
                         locations: dict[str, str]) -> None:
        self.object_holders = dict(holders)
        self.object_locations = dict(locations)

    # ---------- observe ----------
    def observe_tick(self, tick_record: dict,
                     object_facts: dict[str, list[str]]) -> dict:
        tick = tick_record["tick"]
        self.state.tick = tick
        delivered: dict[str, list[str]] = {}
        for d in tick_record.get("deliveries", []):
            for eid in d.get("delivered_event_ids", []):
                delivered.setdefault(eid, []).append(d["agent_id"])
        developed_any = False
        plot_seen = False
        for event in tick_record.get("events", []):
            self.known_event_ids.add(event["event_id"])
            if event.get("type") in PLOT_TYPES:
                plot_seen = True
            # Phase 2 tracking: who asserted which proposition
            if event.get("stance") == "assert" and \
                    event.get("proposition") and event.get("actors"):
                prop = event["proposition"]
                self.generator.assert_speakers.setdefault(
                    prop, set()).add(event["actors"][0])
                self.generator.assert_events.setdefault(
                    prop, []).append(event["event_id"])
            # Phase 2 tracking: object taken without holder noticing
            if event.get("type") == "search":
                finder = (event.get("actors") or [None])[0]
                for obj in event.get("found") or []:
                    holder = self.object_holders.get(obj)
                    if holder and finder and holder != finder and \
                            holder not in delivered.get(
                                event["event_id"], []):
                        self.generator.taken_objects.append({
                            "object": obj, "find_event": event["event_id"],
                            "from_location":
                                self.object_locations.get(obj, ""),
                            "threads": [t.thread_id for t in
                                        self.state.threads.values()
                                        if obj in t.objects]})
                    if finder:
                        self.object_holders[obj] = finder
            developed = self.threads.observe_event(
                event, delivered.get(event["event_id"], []), tick,
                object_facts)
            if developed:
                developed_any = True
                # cross-thread: an event developing 2+ threads is a
                # shared (crossing) event
                if len(developed) > 1:
                    self.threads.mark_shared_event(event["event_id"],
                                                   developed)
            self.conflicts.observe_event(event, tick)
        self.threads.end_of_tick(tick)
        self.conflicts.end_of_tick(tick)
        self.conflicts.detect(tick)
        # stagnation signals (deterministic)
        s = self.state
        s.ticks_since_plot_event = 0 if plot_seen else \
            s.ticks_since_plot_event + 1
        s.ticks_since_thread_progress = 0 if developed_any else \
            s.ticks_since_thread_progress + 1
        # belief changes are not visible at this layer in Phase 1;
        # the counter is fed by the runner integration in Phase 2.
        return {"tick": tick, "developed": developed_any,
                "plot_seen": plot_seen}

    # ---------- Phase 2: propose ----------
    def propose(self, snapshot: WorldSnapshot) -> list[EventCandidate]:
        """Run the candidate pipeline for the current tick. Returns
        validated, evaluated, rank-ordered proposals. Applies the
        cooldown restraint; rejected candidates are still logged."""
        tick = self.state.tick
        if tick - self.last_proposal_tick < \
                self.config.proposal_cooldown:
            return []
        causal = CausalValidator(self.state, self.known_event_ids)
        world = WorldRuleValidator(snapshot, self.seen_fingerprints)
        evaluator = DramaticEvaluator(self.state,
                                      self.seen_fingerprints)
        proposals: list[EventCandidate] = []
        for cand in self.generator.generate(tick):
            ok_c, why_c = causal.validate(cand)
            ok_w, why_w = (world.validate(cand) if ok_c
                           else (False, "skipped"))
            cand.validation = {"causal": why_c, "world": why_w,
                               "passed": bool(ok_c and ok_w)}
            if ok_c and ok_w:
                cand.evaluation = evaluator.evaluate(cand, tick)
                self.seen_fingerprints.add(cand.fingerprint)
                proposals.append(cand)
            self.candidates_log.append(cand)
            self.state.change_log.append(
                {"tick": tick, "kind": "candidate_" +
                 ("proposed" if ok_c and ok_w else "rejected"),
                 "detail": f"{cand.candidate_id} {cand.kind} "
                           f"causal={why_c} world={why_w}"})
        proposals.sort(key=lambda c: -c.evaluation.get("rank", 0))
        if proposals:
            self.last_proposal_tick = tick
        return proposals

    # ---------- replay ----------
    @classmethod
    def replay(cls, run_dir: str | Path, config: StoryConfig,
               characters_root: str | Path | None = None
               ) -> "StoryEngine":
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
        engine = cls(config, characters_root)
        engine.set_object_state(
            {oid: od.get("holder") for oid, od in
             (scenario.get("objects") or {}).items() if od.get("holder")},
            {oid: od.get("location") for oid, od in
             (scenario.get("objects") or {}).items()
             if od.get("location")})
        engine._scenario_locations = list(scenario.get("locations")
                                          or [])
        world_log = json.loads((run_dir / "world_log.json")
                               .read_text(encoding="utf-8"))
        for tick_record in world_log:
            for event in tick_record.get("events", []):
                claim = event.get("claim")
                if claim and claim in claims:
                    cdef = claims[claim]
                    if "proposition" not in event:
                        event["proposition"] = cdef.get("proposition")
                        event["stance"] = cdef.get("stance")
                    # direct evidence: the claim's speaker speaks from
                    # experienced knowledge of the proposition
                    if cdef.get("speaker_kind") == "experienced":
                        event["_direct"] = True
            engine.observe_tick(tick_record, object_facts)
        return engine

    def snapshot(self) -> WorldSnapshot:
        return WorldSnapshot(
            locations=list(getattr(self, "_scenario_locations", [])),
            characters=list(self.config.characters),
            object_locations=dict(self.object_locations))

    @classmethod
    def replay_with_proposals(cls, run_dir: str | Path,
                              config: StoryConfig,
                              characters_root: str | Path | None = None
                              ) -> "StoryEngine":
        """Shadow mode: replay a finished run, running the propose
        pipeline after every tick. Nothing is committed anywhere —
        the run's world_log is only read."""
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
        engine = cls(config, characters_root)
        engine.set_object_state(
            {oid: od.get("holder") for oid, od in
             (scenario.get("objects") or {}).items() if od.get("holder")},
            {oid: od.get("location") for oid, od in
             (scenario.get("objects") or {}).items()
             if od.get("location")})
        engine._scenario_locations = list(scenario.get("locations")
                                          or [])
        world_log = json.loads((run_dir / "world_log.json")
                               .read_text(encoding="utf-8"))
        for tick_record in world_log:
            for event in tick_record.get("events", []):
                claim = event.get("claim")
                if claim and claim in claims:
                    cdef = claims[claim]
                    if "proposition" not in event:
                        event["proposition"] = cdef.get("proposition")
                        event["stance"] = cdef.get("stance")
                    if cdef.get("speaker_kind") == "experienced":
                        event["_direct"] = True
            engine.observe_tick(tick_record, object_facts)
            engine.propose(engine.snapshot())
        return engine

    def summary(self) -> dict:
        s = self.state
        return {
            "story_id": s.story_id, "tick": s.tick,
            "threads": {tid: {"status": t.status,
                              "progress": round(t.progress, 3),
                              "events": len(t.related_events),
                              "known_by": {c: len(f) for c, f
                                           in t.known_by.items()}}
                        for tid, t in s.threads.items()},
            "conflicts": {cid: {"type": c.conflict_type,
                                 "status": c.status,
                                 "severity": round(c.severity, 3),
                                 "escalation": round(c.escalation, 3),
                                 "participants": c.participants}
                          for cid, c in s.conflicts.items()},
            "stagnation": {
                "ticks_since_plot_event": s.ticks_since_plot_event,
                "ticks_since_thread_progress":
                    s.ticks_since_thread_progress},
            "changes": len(s.change_log),
        }
