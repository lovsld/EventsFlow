"""Phase 2: causal event generation (task book §7 + §8).

Pipeline: generators propose EventCandidates -> Causal Validation
-> World Rule Validation -> Dramatic Evaluation -> ranked proposals.
Nothing here commits anything: a validated candidate is converted to
an ordinary world-event dict and handed BACK to the caller (the
World Engine's owner). The Story Engine has no world handle.

Generators are deterministic and template-based in rule mode:
- G-A story_driven / pressure: an escalating thread whose sensitive
  fact has been asserted by >= 2 distinct speakers, and whose config
  declares a pressure_event -> that happening (e.g. an off-screen
  figure's agent arrives). Basis: the asserting events.
- G-B story_driven / opportunity: a dormant thread with an
  unanswered question where another involved character already knows
  a relevant fact -> an opportunity_notice where paths may cross.
  Basis: the thread's related events + the find/assert that gave the
  knower the fact is not required — the thread's own history is.
- G-C world_driven / consequence: a movable object was found by
  someone other than its holder and the holder was NOT delivered
  the find -> a trace_event at the object's original location
  (the place shows signs of having been searched). Basis: the find.

Restraint (§7.5): proposals only on trigger conditions, at most one
per cooldown window, fingerprints never repeat.
"""
from __future__ import annotations

import hashlib

from v03.story.models import EventCandidate, StoryState


def fingerprint(kind: str, thread_id: str, key: str) -> str:
    raw = f"{kind}|{thread_id}|{key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class WorldSnapshot:
    """What validation may look at: locations, characters, object
    holders/locations. Built by the caller from committed state."""

    def __init__(self, locations: list[str], characters: list[str],
                 object_locations: dict[str, str] | None = None):
        self.locations = set(locations)
        self.characters = set(characters)
        self.object_locations = object_locations or {}


class CausalValidator:
    """Every causal_basis event must exist in committed history;
    every related thread must exist; triggering conditions are
    re-checked against the story state, not trusted."""

    def __init__(self, state: StoryState, known_event_ids: set[str]):
        self.state = state
        self.known = known_event_ids

    def validate(self, cand: EventCandidate) -> tuple[bool, str]:
        if not cand.causal_basis:
            return False, "no causal basis"
        for eid in cand.causal_basis:
            if eid not in self.known:
                return False, f"basis event {eid} not in history"
        for tid in cand.related_threads:
            if tid not in self.state.threads:
                return False, f"unknown thread {tid}"
        for cond in cand.triggering_conditions:
            if cond.startswith("thread_escalating:"):
                t = self.state.threads[cond.split(":", 1)[1]]
                if t.status != "escalating":
                    return False, f"condition {cond} no longer holds"
            if cond.startswith("thread_dormant:"):
                t = self.state.threads[cond.split(":", 1)[1]]
                if t.status != "dormant":
                    return False, f"condition {cond} no longer holds"
        return True, "ok"


class WorldRuleValidator:
    def __init__(self, snapshot: WorldSnapshot,
                 seen_fingerprints: set[str]):
        self.snap = snapshot
        self.seen = seen_fingerprints

    def validate(self, cand: EventCandidate) -> tuple[bool, str]:
        if cand.proposed_location not in self.snap.locations:
            return False, f"unknown location {cand.proposed_location}"
        for char in cand.involved_characters:
            if char not in self.snap.characters:
                return False, f"unknown character {char}"
        if cand.fingerprint in self.seen:
            return False, "duplicate candidate (fingerprint seen)"
        loc = cand.expected_world_changes.get("object_location")
        if loc:
            obj, dest = loc
            if dest not in self.snap.locations:
                return False, f"object destination {dest} unknown"
        return True, "ok"


class DramaticEvaluator:
    """Task book §8: dimensions reported separately; the weighted
    rank is for ORDERING only and never deletes or alters history."""

    WEIGHTS = {"causality": 0.25, "thread_progress": 0.2,
               "information_change": 0.15, "conflict_impact": 0.15,
               "goal_impact": 0.1, "novelty": 0.15}

    def __init__(self, state: StoryState, seen_fingerprints: set[str]):
        self.state = state
        self.seen = seen_fingerprints

    def evaluate(self, cand: EventCandidate, tick: int) -> dict:
        s = self.state
        causality = min(1.0, len(cand.causal_basis) / 3.0)
        thread_progress = 0.0
        for tid in cand.related_threads:
            t = s.threads[tid]
            quiet = tick - t.last_developed_at
            thread_progress = max(thread_progress,
                                  1.0 if quiet >= 3 else 0.4)
        conflicts = [s.conflicts[c] for c in cand.related_conflicts
                     if c in s.conflicts]
        conflict_impact = max((c.severity for c in conflicts),
                              default=0.0)
        goal_impact = 0.0
        thread_props = {p for tid in cand.related_threads
                        for p in s.threads[tid].propositions}
        for g in s.goals:
            if thread_props & (set(g.success_beliefs) |
                               set(g.failure_beliefs)):
                goal_impact = max(goal_impact, g.priority)
        information_change = 0.8 if cand.kind in (
            "external_arrival", "trace_event") else 0.4
        novelty = 0.0 if cand.fingerprint in self.seen else 1.0
        dims = {"causality": round(causality, 3),
                "thread_progress": round(thread_progress, 3),
                "conflict_impact": round(conflict_impact, 3),
                "goal_impact": round(goal_impact, 3),
                "information_change": information_change,
                "novelty": novelty,
                "coherence": 1.0}
        rank = sum(dims[k] * w for k, w in self.WEIGHTS.items()
                   if k in dims)
        dims["rank"] = round(rank, 3)
        return dims


class EventGenerator:
    """Deterministic template generators over the story state and
    the engine's tracked world facts (object finds, assertions)."""

    def __init__(self, state: StoryState):
        self.state = state
        self._counter = 0
        # tracked from the observe stream by StoryEngine:
        self.assert_speakers: dict[str, set[str]] = {}
        self.assert_events: dict[str, list[str]] = {}
        self.taken_objects: list[dict] = []

    def _new_id(self) -> str:
        self._counter += 1
        return f"EC{self._counter:03d}"

    def generate(self, tick: int) -> list[EventCandidate]:
        out: list[EventCandidate] = []
        for gen in (self._gen_pressure, self._gen_opportunity,
                    self._gen_trace):
            cand = gen(tick)
            if cand:
                out.append(cand)
        return out

    def _gen_pressure(self, tick: int) -> EventCandidate | None:
        for t in self.state.threads.values():
            pe = t.pressure_event
            if not pe or t.status != "escalating":
                continue
            for fact in t.sensitive_facts:
                speakers = self.assert_speakers.get(fact, set())
                if len(speakers) >= 2:
                    fp = fingerprint("external_arrival", t.thread_id,
                                     fact)
                    return EventCandidate(
                        candidate_id=self._new_id(),
                        source_type="story_driven",
                        kind=pe.get("kind", "external_arrival"),
                        description=pe.get("description", ""),
                        proposed_location=pe.get("location", ""),
                        triggering_conditions=[
                            f"thread_escalating:{t.thread_id}",
                            f"sensitive_fact_multi_speaker:{fact}"],
                        causal_basis=list(
                            self.assert_events.get(fact, []))[-4:],
                        related_threads=[t.thread_id],
                        related_conflicts=[
                            c.conflict_id for c in
                            self.state.conflicts.values()
                            if t.thread_id in c.related_threads],
                        expected_world_changes={
                            "arrival": pe.get("figure", "")},
                        dramatic_functions=["escalation", "consequence"],
                        uncertainty=0.4,
                        validity_requirements=[
                            "location exists", "no duplicate arrival"],
                        fingerprint=fp, created_at=tick)
        return None

    def _gen_opportunity(self, tick: int) -> EventCandidate | None:
        for t in self.state.threads.values():
            if t.status != "dormant":
                continue
            unanswered = [q for q in t.questions
                          if q.answered_at is None]
            if not unanswered or not t.related_events:
                continue
            knowers = [c for c in t.involved_characters
                       if t.known_by.get(c)]
            oe = t.opportunity_event
            if not knowers or not oe:
                continue
            fp = fingerprint("opportunity_notice", t.thread_id,
                             unanswered[0].id)
            return EventCandidate(
                candidate_id=self._new_id(), source_type="story_driven",
                kind="opportunity_notice",
                description=oe.get("description", ""),
                proposed_location=oe.get("location", ""),
                triggering_conditions=[f"thread_dormant:{t.thread_id}"],
                causal_basis=list(t.related_events)[-3:],
                related_threads=[t.thread_id],
                expected_world_changes={"gathering": "town_square"},
                dramatic_functions=["setup"],
                uncertainty=0.6,
                validity_requirements=["location exists"],
                fingerprint=fp, created_at=tick)
        return None

    def _gen_trace(self, tick: int) -> EventCandidate | None:
        for taken in self.taken_objects:
            if taken.get("trace_proposed"):
                continue
            fp = fingerprint("trace_event", taken["object"],
                             taken["find_event"])
            taken["trace_proposed"] = True
            return EventCandidate(
                candidate_id=self._new_id(), source_type="world_driven",
                kind="trace_event",
                description="此处留下了被人翻动过的痕迹。",
                proposed_location=taken["from_location"],
                triggering_conditions=[
                    f"object_taken_unnoticed:{taken['object']}"],
                causal_basis=[taken["find_event"]],
                related_threads=list(taken.get("threads", [])),
                expected_world_changes={
                    "trace_at": taken["from_location"]},
                dramatic_functions=["consequence", "revelation"],
                uncertainty=0.3,
                validity_requirements=["location exists"],
                fingerprint=fp, created_at=tick)
        return None


def to_world_event(cand: EventCandidate, event_id: str) -> dict:
    """The ordinary world-event dict a validated candidate becomes.
    Returned to the caller; committing it is the World Engine's act,
    never this layer's."""
    return {"event_id": event_id, "type": "ambient",
            "subtype": cand.kind, "actors": [],
            "location": cand.proposed_location,
            "text": cand.description,
            "source": "story_engine", "candidate_id": cand.candidate_id,
            "causal_basis": list(cand.causal_basis)}
