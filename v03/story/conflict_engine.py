"""Conflict Engine (task book §6): deterministic detection of
goal / resource / information conflicts, plus escalation bookkeeping
driven by committed events.

Detectors (no LLM in Phase 1):
- goal: two characters whose goal conditions pull the same belief in
  opposite directions (A succeeds when belief B rises, C fails when
  belief B rises — their interests cannot both be satisfied), plus
  conflicts declared in the story config.
- resource: two characters' goals declaring the same required
  resource (story-config goal metadata).
- information: an involved character of a thread lacks a sensitive
  fact of that thread that another involved character knows
  (asymmetry is the conflict substrate, task book §6.2).

The engine never forces confrontation: conflicts may stay latent,
escalate through events, cool down, or resolve when their thread
resolves. Escalation rises on confrontations and on sensitive
assertions between participants; it decays over quiet ticks.
"""
from __future__ import annotations

from v03.story.models import CharacterConflict, StoryState

DECAY_PER_QUIET_TICK = 0.02
ESCALATE_ON_CONFRONT = 0.15
ESCALATE_ON_SENSITIVE = 0.08


class ConflictEngine:
    def __init__(self, state: StoryState):
        self.state = state
        self._counter = 0

    def _new_id(self) -> str:
        self._counter += 1
        return f"C{self._counter:03d}"

    # ---------- detection ----------
    def detect(self, tick: int) -> list[CharacterConflict]:
        found: list[CharacterConflict] = []
        found += self._detect_goal_conflicts(tick)
        found += self._detect_resource_conflicts(tick)
        found += self._detect_information_conflicts(tick)
        return found

    def _exists(self, ctype: str, participants: list[str],
                key: str) -> bool:
        for c in self.state.conflicts.values():
            if c.conflict_type == ctype and \
                    sorted(c.participants) == sorted(participants) and \
                    key in c.conflicting_interests:
                return True
        return False

    def _register(self, conflict: CharacterConflict,
                  tick: int) -> CharacterConflict:
        self.state.conflicts[conflict.conflict_id] = conflict
        self.state.change_log.append(
            {"tick": tick, "kind": "conflict_detected",
             "detail": f"{conflict.conflict_id} {conflict.conflict_type} "
                       f"{conflict.participants} ({conflict.basis})"})
        return conflict

    def _detect_goal_conflicts(self, tick: int) -> list[CharacterConflict]:
        out: list[CharacterConflict] = []
        goals = self.state.goals
        for i, a in enumerate(goals):
            for b in goals[i + 1:]:
                if a.character_id == b.character_id:
                    continue
                shared = set(a.success_beliefs) & set(b.failure_beliefs)
                shared |= set(b.success_beliefs) & set(a.failure_beliefs)
                for belief in sorted(shared):
                    key = f"belief:{belief}"
                    if self._exists("goal",
                                    [a.character_id, b.character_id], key):
                        continue
                    out.append(self._register(CharacterConflict(
                        conflict_id=self._new_id(), conflict_type="goal",
                        participants=[a.character_id, b.character_id],
                        goals_involved=[a.goal_id, b.goal_id],
                        conflicting_interests=[key],
                        severity=0.5, status="latent",
                        basis=f"{a.goal_id} and {b.goal_id} pull belief "
                              f"{belief} in opposite directions"), tick))
        return out

    def _detect_resource_conflicts(self,
                                   tick: int) -> list[CharacterConflict]:
        out: list[CharacterConflict] = []
        by_resource: dict[str, list] = {}
        for g in self.state.goals:
            for r in g.required_resources:
                by_resource.setdefault(r, []).append(g)
        for resource, gs in sorted(by_resource.items()):
            chars = sorted({g.character_id for g in gs})
            if len(chars) < 2:
                continue
            key = f"resource:{resource}"
            if self._exists("resource", chars, key):
                continue
            out.append(self._register(CharacterConflict(
                conflict_id=self._new_id(), conflict_type="resource",
                participants=chars,
                goals_involved=[g.goal_id for g in gs],
                conflicting_interests=[key], severity=0.4,
                status="latent",
                basis=f"goals compete for resource {resource}"), tick))
        return out

    def _detect_information_conflicts(
            self, tick: int) -> list[CharacterConflict]:
        out: list[CharacterConflict] = []
        for t in self.state.threads.values():
            for fact in t.sensitive_facts:
                knowers = [c for c in t.involved_characters
                           if fact in t.known_by.get(c, [])]
                lacking = [c for c in t.involved_characters
                           if fact not in t.known_by.get(c, [])]
                if not knowers or not lacking:
                    continue
                key = f"asymmetry:{t.thread_id}:{fact}"
                parts = sorted(set(knowers + lacking))
                if self._exists("information", parts, key):
                    continue
                out.append(self._register(CharacterConflict(
                    conflict_id=self._new_id(),
                    conflict_type="information", participants=parts,
                    conflicting_interests=[key],
                    related_threads=[t.thread_id], severity=0.35,
                    status="latent",
                    basis=f"{knowers} know {fact} in thread "
                          f"{t.thread_id}; {lacking} do not"), tick))
        return out

    def add_declared(self, initial, tick: int) -> CharacterConflict:
        return self._register(CharacterConflict(
            conflict_id=self._new_id(),
            conflict_type=initial.conflict_type,
            participants=list(initial.participants),
            goals_involved=list(initial.goals_involved),
            conflicting_interests=list(initial.conflicting_interests),
            related_threads=list(initial.related_threads),
            severity=initial.severity, status="latent",
            basis=initial.basis), tick)

    # ---------- event-driven escalation ----------
    def observe_event(self, event: dict, tick: int) -> None:
        actors = event.get("actors") or []
        if len(actors) < 2:
            return
        pair = {actors[0], actors[1]}
        for c in self.state.conflicts.values():
            if c.status == "resolved":
                continue
            if not pair <= set(c.participants):
                continue
            bump = 0.0
            if event.get("type") == "confront":
                bump = ESCALATE_ON_CONFRONT
            elif event.get("stance") == "assert" and \
                    event.get("proposition") and \
                    any(event["proposition"] in
                        self.state.threads[t].sensitive_facts
                        for t in c.related_threads
                        if t in self.state.threads):
                bump = ESCALATE_ON_SENSITIVE
            if bump:
                c.escalation = min(1.0, c.escalation + bump)
                c.last_touched_at = tick
                if event["event_id"] not in c.related_events:
                    c.related_events.append(event["event_id"])
                if c.escalation >= 0.3 and c.status == "latent":
                    c.status = "active"
                if c.escalation >= 0.6:
                    c.status = "escalating"
                c.severity = min(1.0, c.severity + bump / 2)

    def end_of_tick(self, tick: int) -> None:
        for c in self.state.conflicts.values():
            if c.status == "resolved":
                continue
            if tick > c.last_touched_at:
                c.escalation = max(0.0, c.escalation -
                                    DECAY_PER_QUIET_TICK)
                if c.escalation < 0.3 and c.status == "escalating":
                    c.status = "active"
            # resolution follows the linked thread
            for tid in c.related_threads:
                t = self.state.threads.get(tid)
                if t and t.status == "resolved":
                    c.status = "resolved"
                    self.state.change_log.append(
                        {"tick": tick, "kind": "conflict_resolved",
                         "detail": f"{c.conflict_id} via thread {tid}"})
                    break
