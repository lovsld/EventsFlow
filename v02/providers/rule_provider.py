"""Rule Provider — the Generic Candidate Generator (V0.2-B, design 3.6).

A complete rewrite of the V0.1-port rule provider. There are NO
per-character branches, NO claim-name branches and NO location-name
branches in this file: everything scenario-specific arrives as DATA —
the run's Scenario Definition (claim classes, object declarations,
speech lines), the character's own goals (as structured conditions),
its profile (personality, disclosure threshold, routine) and its
private state (beliefs, trust, holdings, history).

Candidates are generated from four sources:
  1. goal-driven — reverse templates from the UNSATISFIED leaves of
     the selected goal's success_condition (the same leaves the
     GoalEvaluator scores), plus a synthesized plan when the goal
     has none serving it;
  2. plan-step — templates from the current structured plan step;
  3. social — pending-question responses priced by the disclosure
     gate (trust in the asker vs the agent's own disclosure
     threshold), greetings;
  4. baseline — observe / listen / work (routine from the profile) /
     wait.

Scoring is one utility function for every candidate:
  Utility = base(source)
          + wG*GoalAlignment + wI*InformationValue
          + wP*PersonalityPreference + wE*EmotionInfluence
          + PLAN_BONUS*PlanConsistency
          - wR*RiskCost - wS*SocialCost - RepetitionPenalty
          + jitter (seeded, <= 0.18)
The top-5 scored candidates and the final choice are persisted on
the decision record (candidate_scores), so any rule-mode decision
can be re-derived and audited after the fact. InformationValue is
scored by the agent's OWN uncertainty — the same action is worth
different amounts to differently-informed agents, which is what
keeps behaviour from homogenizing across the cast.

Determinism: all enumeration is sorted(); the only randomness is the
seeded per-agent RNG's jitter, drawn in signature order.
"""
from __future__ import annotations

import random
from collections import deque

from ..comm_candidates import (generate_comm_candidates,
                               proposition_held_since, undelivered_duties)
from ..conditions import (SubjectiveView, all_leaves, evaluate,
                          flatten_leaves, last_seen_location)
from ..info_value import SENSOR_IV, URGENCY_IV, searched_certainty
from ..context_builder import action_is_offered
from ..protocol import (ActionSpec, AgentDecision, DecisionContext, PlanStep,
                        PlanUpdate, ProviderResult, RetryPolicy)
from ..scenario import default_scenario
from .base import LLMProvider

# Utility weights (generic coefficients — design 3.6).
W_GOAL = 0.9
W_INFO = 0.55
W_PERSONALITY = 0.45
W_EMOTION = 0.40
PLAN_BONUS = 0.35
W_RISK = 0.8
W_SOCIAL = 0.8
JITTER_MAX = 0.18

# V0.2-C C2 (design 3.3): the first-hand disclosure cost
# coefficient for ANSWER candidates. The design's default was
# 0.6 ("answering is a social obligation, cheaper than
# volunteering's 1.6"), with the value sent through the
# section-2.8 calibration method and the tavern secrecy chain as
# a hard constraint. Calibration result (c2_calibration.py,
# grid {0.6, 1.2, 1.8, 2.2, 2.6}, winner = smallest survivor):
# the flat recipient-side info repricing (+0.55 over the old
# speaker-side value) more than consumes the 0.6 discount, so
# the chain only survives at 2.6 — the coefficient ends up ABOVE
# the volunteering coefficient, an artefact of combining the
# flat info rule with the disclosure fixture's threshold gap;
# the calibrated NET answer pricing is still more favourable
# than B's in the clinic case the design measured (doctor's
# answer total 0.97-1.00 under B vs ~1.19-1.32 here). Full
# account in docs/V0.2-C_IMPLEMENTATION_REPORT.md (C2 section).
ANSWER_DISCLOSURE_COEF = 2.6
BASE_SCORE = {"goal": 0.35, "plan": 0.35, "social": 0.30,
              "greeting": 0.25, "baseline": 0.20}
BASELINE_BASE = {"observe": 0.30, "listen": 0.22, "wait": 0.08}
REPETITION_HEAVY = 0.22     # speak/ask/confront/show/search per prior use
REPETITION_LIGHT = 0.05
CONFRONT_MIN_BASIS = 0.55   # a confrontation asserts only well-held beliefs
DIRECT_TIER = 3


def _sig(action: dict) -> str:
    return (f"{action.get('type')}:"
            f"{action.get('target') or action.get('target_location') or ''}:"
            f"{action.get('topic') or action.get('object') or ''}")


class RuleProvider(LLMProvider):
    name = "rule"
    model = "rule-generic-v0.2b"

    def __init__(self, config, seed: int = 42):
        super().__init__(config, seed)
        self.rng = random.Random(seed + config.seed_offset)
        # The run's Scenario Definition; the runner replaces this with
        # the run's instance (design 3.7 threading). The tavern
        # default keeps standalone construction (unit tests) working.
        self.scenario = default_scenario()

    # ---------------- subjective view over the context ----------------
    def _view(self, context: DecisionContext) -> SubjectiveView:
        return SubjectiveView(
            agent_id=context.character_id, tick=context.tick,
            location=context.location,
            beliefs={b.key: b for b in context.beliefs},
            trust=dict(getattr(context, "trust", {}) or {}),
            holdings=set(getattr(context, "holdings", []) or []),
            found_objects=set(getattr(context, "found_objects", []) or []),
            searched_empty_places=set(
                getattr(context, "searched_empty_places", []) or []),
            object_sightings=dict(getattr(context, "object_sightings", {}) or {}),
            perceived_events=list(getattr(context, "perceived_events", []) or []),
            own_actions=list(getattr(context, "own_actions", []) or []),
            goals={**{g.key: g.status for g in context.active_goals},
                   **({context.pursuit_goal.key: context.pursuit_goal.status}
                      if getattr(context, "pursuit_goal", None) is not None
                      else {})})

    # ---------------- small helpers ----------------
    def _conf(self, view: SubjectiveView, key: str) -> float:
        b = view.beliefs.get(key)
        return b.confidence if b else 0.0

    def _kind(self, view: SubjectiveView, key: str) -> str | None:
        b = view.beliefs.get(key)
        return b.kind if b else None

    def _trust(self, view: SubjectiveView, other: str) -> float:
        return view.trust.get(other, 0.3)

    def _colocated(self, context: DecisionContext) -> list[str]:
        return sorted(c["id"] for c in context.perception.get("colocated", []))

    def _adjacency(self, context: DecisionContext) -> dict[str, list[str]]:
        sc = getattr(context, "scenario", None) or {}
        return sc.get("adjacency") or self.scenario.adjacency

    def _next_hop(self, context: DecisionContext, targets: set[str]) -> str | None:
        """First step on a shortest path from here toward any target —
        breadth-first over the scenario graph, neighbours sorted."""
        start = context.location
        if start in targets:
            return None
        adj = self._adjacency(context)
        prev: dict[str, str | None] = {start: None}
        q = deque([start])
        while q:
            cur = q.popleft()
            for nb in sorted(adj.get(cur, [])):
                if nb not in prev:
                    prev[nb] = cur
                    if nb in targets:
                        node = nb
                        while prev[node] != start:
                            node = prev[node]  # type: ignore[index]
                        return node
                    q.append(nb)
        return None

    def _line(self, claim: str | None, counts: dict) -> str:
        lines = self.scenario.speech_lines.get(claim) if claim else None
        if lines is None:
            lines = "{name}：……"
        if isinstance(lines, list):
            n = sum(v for k, v in counts.items()
                    if claim and k.endswith(f":{claim}"))
            lines = lines[n % len(lines)]
        return str(lines).format(name=self.config.name)

    def _eligible_assert(self, view: SubjectiveView, claim_key: str,
                         floor: float = 0.0) -> bool:
        """May this agent assert this claim class? The claim's
        declared speaker basis must be held at the declared floor
        (and kind, when declared). Basis-free claims (proposals,
        bluffs) are always voiceable — the SocialCost honesty factor
        prices them."""
        cdef = self.scenario.claims.get(claim_key)
        if cdef is None or cdef.stance != "assert":
            return False
        basis = cdef.basis_beliefs()
        if not basis:
            return True
        b = view.beliefs.get(basis[0])
        if b is None:
            return False
        need = max(cdef.speaker_min_confidence, floor)
        if b.confidence < need:
            return False
        if cdef.speaker_kind and b.kind != cdef.speaker_kind:
            return False
        return True

    def _assert_claim_for(self, view: SubjectiveView, prop: str,
                          floor: float = 0.0) -> str | None:
        """The first assert claim class for a proposition that THIS
        speaker is eligible to voice (per-claim speaker bases differ:
        a first-person report and a second-hand hint are different
        classes for the same proposition)."""
        for key in self.scenario.claims_for_proposition(prop, "assert"):
            if self._eligible_assert(view, key, floor=floor):
                return key
        return None

    # ---------------- candidate plumbing ----------------
    def _cand(self, cands: list, action: dict, *, source: str,
              goal_key: str | None = None, leaf: dict | None = None,
              prop: str | None = None, obj: str | None = None,
              dest_place: str | None = None) -> None:
        cands.append({"action": action, "source": source,
                      "goal_key": goal_key, "leaf": leaf,
                      "prop": prop, "obj": obj, "dest_place": dest_place})

    def _speech(self, cands, verb: str, target: str, claim: str, *,
                source: str, goal_key=None, leaf=None, counts=None) -> None:
        self._cand(cands,
                   {"type": verb, "target": target, "topic": claim,
                    "claim": claim,
                    "content": self._line(claim, counts or {})},
                   source=source, goal_key=goal_key, leaf=leaf,
                   prop=self.scenario.claims[claim].proposition
                   if claim in self.scenario.claims else None)

    def _search_route(self, cands, view: SubjectiveView,
                      context: DecisionContext, obj: str | None, *,
                      source: str, goal_key=None, leaf=None) -> None:
        """Search where the object might be — my sighting if I have
        one, else every searchable place — moving first when the
        place is not here."""
        places: list[str] = []
        if obj and view.object_sightings.get(obj):
            places = [view.object_sightings[obj]]
        else:
            places = self.scenario.searchable_locations()
        for place in sorted(set(places)):
            if place == context.location:
                self._cand(cands, {"type": "search",
                                   "target_location": place},
                           source=source, goal_key=goal_key, leaf=leaf,
                           obj=obj, dest_place=place)
            else:
                hop = self._next_hop(context, {place})
                if hop:
                    self._cand(cands, {"type": "move",
                                       "target_location": hop},
                               source=source, goal_key=goal_key, leaf=leaf,
                               obj=obj, dest_place=place)

    # ---------------- leaf -> candidate templates ----------------
    def _leaf_candidates(self, cands, leaf: dict, view: SubjectiveView,
                         context: DecisionContext, *, source: str,
                         goal_key: str | None, counts: dict) -> None:
        sc = self.scenario
        me = context.character_id
        colocated = self._colocated(context)
        t = leaf.get("type")
        if t in ("object_possession", "object_found", "object_at_location"):
            obj = leaf["object"]
            holder = leaf.get("holder", "self")
            if t == "object_at_location":
                self._search_route(cands, view, context, obj, source=source,
                                   goal_key=goal_key, leaf=leaf)
            elif holder in ("self", me):
                self._search_route(cands, view, context, obj, source=source,
                                   goal_key=goal_key, leaf=leaf)
            else:
                # someone else should hold it: ask / observe are the
                # subjective levers — plus, V0.2-C C3 (design 4.2.1),
                # a search route when I do NOT strongly believe the
                # holder holds it: "not sure it is in their hands —
                # search the searchable places first". The strong-
                # belief suppression (conf >= 0.9 on the holds:
                # belief) is subjective, not plot knowledge.
                for other in colocated:
                    self._cand(cands, {"type": "observe", "target": other},
                               source=source, goal_key=goal_key, leaf=leaf,
                               obj=obj)
                if self._conf(view, f"holds:{holder}:{obj}") < 0.9 \
                        and sc.objects.get(obj) is not None \
                        and sc.searchable_locations():
                    self._search_route(cands, view, context, obj,
                                       source=source, goal_key=goal_key,
                                       leaf=leaf)
            decl = sc.objects.get(obj)
            if decl is not None and decl.fact_belief:
                claim = sc.claim_for_proposition(decl.fact_belief, "inquire")
                if claim:
                    for other in colocated:
                        self._speech(cands, "ask", other, claim,
                                     source=source, goal_key=goal_key,
                                     leaf=leaf, counts=counts)
        elif t == "belief_threshold":
            prop = leaf["belief"]
            theta = float(leaf.get("min_confidence", 1.0))
            tier = leaf.get("min_source_tier")
            need_direct = tier is not None and (
                (isinstance(tier, int) and tier >= DIRECT_TIER)
                or tier in ("direct", "direct_observation", "experienced"))
            obj = sc.object_for_fact_belief(prop)
            if need_direct and obj is not None:
                # only first-hand contact satisfies a direct-tier
                # leaf: go and find the object, or show it if held
                self._search_route(cands, view, context, obj, source=source,
                                   goal_key=goal_key, leaf=leaf)
                if obj in view.holdings:
                    for other in colocated:
                        self._cand(cands, {"type": "show", "object": obj,
                                           "target": other},
                                   source=source, goal_key=goal_key,
                                   leaf=leaf, prop=prop, obj=obj)
            inquire = sc.claim_for_proposition(prop, "inquire")
            if inquire:
                cdef = sc.claims[inquire]
                if self._conf(view, prop) >= cdef.speaker_min_confidence \
                        or not cdef.basis_required:
                    for other in colocated:
                        self._speech(cands, "ask", other, inquire,
                                     source=source, goal_key=goal_key,
                                     leaf=leaf, counts=counts)
            assert_claim = self._assert_claim_for(view, prop)
            if assert_claim:
                for other in colocated:
                    self._speech(cands, "speak", other, assert_claim,
                                 source=source, goal_key=goal_key,
                                 leaf=leaf, counts=counts)
                if self._conf(view, prop) >= CONFRONT_MIN_BASIS \
                        and self.config.personality.get("impulsiveness", 0) >= 0.5:
                    confront_claim = self._assert_claim_for(
                        view, prop, floor=CONFRONT_MIN_BASIS) or assert_claim
                    for other in colocated:
                        self._speech(cands, "confront", other, confront_claim,
                                     source=source, goal_key=goal_key,
                                     leaf=leaf, counts=counts)
            for verb in ("observe", "listen"):
                # ambient sensors: leaf-linked for attribution, but
                # they do not TARGET the proposition — no prop tag
                # (their InformationValue is the flat sensor value,
                # and _score discounts their goal alignment)
                self._cand(cands, {"type": verb}, source=source,
                           goal_key=goal_key, leaf=leaf)
            if obj is not None and not need_direct:
                self._search_route(cands, view, context, obj, source=source,
                                   goal_key=goal_key, leaf=leaf)
        elif t == "agent_at_location":
            who = leaf.get("agent", "self")
            if who in ("self", me):
                hop = self._next_hop(context, {leaf["location"]})
                if hop:
                    self._cand(cands, {"type": "move", "target_location": hop},
                               source=source, goal_key=goal_key, leaf=leaf)
            else:
                seen = None
                for ev in view.perceived_events:
                    if ev.get("actor") == who and ev.get("type") == "move":
                        seen = ev.get("to")
                    elif ev.get("actor") == who and ev.get("location"):
                        seen = ev.get("location")
                if seen and seen != context.location:
                    hop = self._next_hop(context, {seen})
                    if hop:
                        self._cand(cands, {"type": "move",
                                           "target_location": hop},
                                   source=source, goal_key=goal_key,
                                   leaf=leaf)
                self._cand(cands, {"type": "observe"}, source=source,
                           goal_key=goal_key, leaf=leaf)
        elif t == "event_occurred":
            claim = leaf.get("claim")
            if claim and claim in sc.claims:
                cdef = sc.claims[claim]
                if cdef.stance == "inquire":
                    for other in colocated:
                        self._speech(cands, "ask", other, claim,
                                     source=source, goal_key=goal_key,
                                     leaf=leaf, counts=counts)
                elif self._eligible_assert(view, claim):
                    for other in colocated:
                        self._speech(cands, "speak", other, claim,
                                     source=source, goal_key=goal_key,
                                     leaf=leaf, counts=counts)
            for verb in ("observe", "listen"):
                self._cand(cands, {"type": verb}, source=source,
                           goal_key=goal_key, leaf=leaf)
        elif t == "trust_threshold":
            social = sc.social_claim()
            for other in colocated:
                if social:
                    self._speech(cands, "speak", other, social,
                                 source=source, goal_key=goal_key,
                                 leaf=leaf, counts=counts)
                # any basis-free propositional claim is also a
                # relationship-building utterance (a proposal, an offer)
                for key in sorted(sc.claims):
                    cdef = sc.claims[key]
                    if cdef.stance == "assert" and cdef.proposition \
                            and not cdef.basis_required:
                        self._speech(cands, "speak", other, key,
                                     source=source, goal_key=goal_key,
                                     leaf=leaf, counts=counts)
        # goal_status leaves: no direct action template (the other
        # goal's own machinery handles it)

    # ---------------- plan synthesis ----------------
    def synthesize_steps(self, goal, view: SubjectiveView) -> list[PlanStep]:
        """A plan for a goal, derived from its success_condition
        leaves in a fixed predicate order — locate before act,
        evidence before conclusion (design 3.6)."""
        leaves = flatten_leaves(goal.success_condition)
        steps: list[PlanStep] = []
        seen: set[str] = set()

        def add(step_id: str, objective: str, templates: list[dict],
                condition: dict) -> None:
            if step_id in seen:
                return
            seen.add(step_id)
            steps.append(PlanStep(
                step_id=step_id, objective=objective,
                action_candidates=templates, success_condition=condition,
                retry_policy=RetryPolicy()))

        obj_leaves = [l for l in leaves if l.get("type") in
                      ("object_possession", "object_found",
                       "object_at_location")]
        belief_leaves = [l for l in leaves
                         if l.get("type") == "belief_threshold"]
        loc_leaves = [l for l in leaves
                      if l.get("type") == "agent_at_location"]
        other_leaves = [l for l in leaves if l not in obj_leaves
                        and l not in belief_leaves and l not in loc_leaves]
        for leaf in obj_leaves:
            obj = leaf["object"]
            add(f"locate:{obj}", f"locate {obj}",
                [{"type": "search"}, {"type": "move"}, {"type": "ask"}],
                {"type": "object_found", "object": obj})
            add(f"secure:{obj}", f"secure {obj}",
                [{"type": "search"}, {"type": "show"}], leaf)
        for leaf in belief_leaves:
            templates = [{"type": "ask"}, {"type": "observe"},
                         {"type": "listen"}, {"type": "confront"}]
            # V0.2-C C4 (design 5.2, the LOCALITY CONTRACT): a
            # belief leaf never stands alone as a step condition.
            # At confidence 0 its satisfaction is 0 indefinitely,
            # so the old gather step could only wait or be counted
            # failed — the step's condition must be reachable by
            # the step's own template actions or the responses
            # they directly cause. The evidence step's condition
            # is therefore the leaf PLUS: one event branch per
            # claim class that asserts the proposition (a
            # co-present other's speak event IS the response the
            # templates solicit), and an object_found branch when
            # the scenario declares an object whose fact the
            # belief is. The goal layer still judges the belief
            # itself — step completion is not goal completion
            # (four-layer separation). When the data offers no
            # asserting claim and no fact object, no response
            # branch exists to mix in and the leaf stands alone
            # (degenerate, data-dependent; registered in the C4
            # report).
            branches: list[dict] = [leaf]
            for claim_key in self.scenario.claims_for_proposition(
                    leaf["belief"], "assert"):
                branches.append(
                    {"type": "event_occurred", "event_type": "speak",
                     "actor": "any_other", "claim": claim_key})
            if self.scenario.object_for_fact_belief(leaf["belief"]):
                branches.append(
                    {"type": "object_found",
                     "object": self.scenario.object_for_fact_belief(
                         leaf["belief"])})
                templates.append({"type": "search"})
            condition = branches[0] if len(branches) == 1 \
                else {"any": branches}
            add(f"evidence:{leaf['belief']}",
                f"gather evidence: {leaf['belief']}", templates, condition)
        for leaf in loc_leaves:
            add(f"go:{leaf['location']}", f"be at {leaf['location']}",
                [{"type": "move"}], leaf)
        for i, leaf in enumerate(other_leaves):
            add(f"advance:{i}:{leaf.get('type')}", f"advance: {leaf.get('type')}",
                [{"type": "speak"}, {"type": "ask"}, {"type": "observe"}], leaf)
        return steps

    def _maybe_synthesize(self, context: DecisionContext,
                          view: SubjectiveView, selected) -> PlanUpdate | None:
        if selected is None or not selected.success_condition:
            return None
        plan = context.current_plan
        if plan is not None:
            if plan.goal_id == selected.key or plan.goal == selected.key:
                return None                      # already serving it
            if plan.status == "blocked":
                return None                      # B3 recovery owns blocked
            if plan.status == "suspended":
                # V0.2-C C1: a suspended plan is PRESERVED state
                # (its attempts/bookkeeping resume on reactivation);
                # synthesis never destroys it. The selected goal is
                # pursued through goal candidates meanwhile.
                return None
            by_key = {g.key: g for g in context.active_goals}
            served = by_key.get(plan.goal_id or plan.goal)
            if served is not None and served.status not in (
                    "completed", "failed", "abandoned"):
                # V0.2-C C1: when the Goal Scheduler has named this
                # goal the pursuit, the handover is the scheduler's
                # decision (its hysteresis already priced the
                # switch) — the priority-gap heuristic only governs
                # unscheduled (legacy) contexts.
                sched_id = getattr(context, "scheduled_pursuit_goal_id",
                                   None)
                if not (sched_id and sched_id == selected.key):
                    gap = selected.priority_current - served.priority_current
                    if gap < 0.05:
                        return None              # not clearly outranked
        steps = self.synthesize_steps(selected, view)
        if not steps:
            return None
        return PlanUpdate(status="replace",
                          reason=f"synthesized plan for goal {selected.key}",
                          new_goal=selected.key, new_steps=steps,
                          goal_id=selected.key)

    # ---------------- scoring ----------------
    def _score(self, cand: dict, context: DecisionContext,
               view: SubjectiveView, selected_key: str | None,
               second_key: str | None, maintenance: list,
               plan_step: dict | None, counts: dict) -> dict:
        action = cand["action"]
        verb = action["type"]
        P = self.config.personality
        emotions = context.emotional_state or {}
        base = BASE_SCORE[cand["source"]]
        if cand["source"] == "baseline":
            base = BASELINE_BASE.get(verb, base)
            if verb == "work":
                base = 0.40 if self.config.routine else 0.25

        goal_f = 0.0
        if cand["goal_key"] is not None:
            if cand["goal_key"] == selected_key:
                goal_f = 1.0
            elif cand["goal_key"] == second_key:
                goal_f = 0.5
        # Generic sensors (observe/listen with nothing targeted) do
        # not pursue a leaf the way a leaf-specific action does:
        # their goal alignment is discounted, or passive watching
        # would drown every concrete step (measured: the first B1
        # tavern run produced 60 observe/listen and zero dialogue).
        if verb in ("observe", "listen") and not cand["prop"] \
                and not cand["obj"] and not action.get("claim"):
            goal_f *= 0.25

        info_f = 0.0
        if cand.get("info_override") is not None:
            # V0.2-C C2/C3 (design 3.3/4.1): recipient-side
            # information value, computed by the candidate's
            # generator (answer = the asker's estimated ignorance;
            # disclose = the duty's urgency; assert = ignorance x
            # relevance) — this REPLACES the speaker-side estimate.
            info_f = cand["info_override"]
        elif cand["prop"]:
            info_f = 1.0 - self._conf(view, cand["prop"])
        elif verb == "search" or (verb == "move" and cand.get("dest_place")):
            # V0.2-C C3 (design 4.1): the unified IV for physical
            # exploration — uncertainty about a place is
            # 1 - searched_certainty (0.9 once I have searched it,
            # from the derived searched-places set, design 4.3);
            # a move that is one leg of a search route carries
            # 0.8 of the search's value, as in B.
            place = cand.get("dest_place") or action.get("target_location") \
                or context.location
            info_f = 1.0 - searched_certainty(view, place)
            if verb == "move":
                info_f *= 0.8
        elif verb in ("observe", "listen"):
            info_f = SENSOR_IV

        pers_map = {
            "search": P.get("curiosity", 0.5),
            "ask": (P.get("curiosity", 0.5) + P.get("sociability", 0.5)) / 2,
            "speak": P.get("sociability", 0.5),
            "confront": P.get("impulsiveness", 0.5),
            "move": P.get("impulsiveness", 0.5) * 0.5 + P.get("curiosity", 0.5) * 0.25,
            "observe": P.get("caution", 0.5),
            "listen": (P.get("caution", 0.5) + P.get("curiosity", 0.5)) / 2,
            "work": 0.5,
            "wait": P.get("caution", 0.5) * 0.5,
            "refuse": P.get("caution", 0.5),
            "show": P.get("sociability", 0.5) * 0.5,
        }
        pers_f = pers_map.get(verb, 0.3)

        suspicion = emotions.get("suspicion", 0.0)
        arousal = emotions.get("arousal", 0.0)
        emotion_f = suspicion * (0.5 if verb in ("search", "confront")
                                 else 0.15 if verb == "ask" else 0.0)
        if verb in ("ask", "confront"):
            emotion_f += arousal * 0.1

        plan_f = 0.0
        if cand["source"] == "plan":
            plan_f = 1.0
        elif plan_step:
            for tmpl in plan_step.get("action_candidates") or []:
                if tmpl.get("type") == verb and all(
                        action.get(k) == v for k, v in tmpl.items()
                        if k != "type"):
                    plan_f = 0.8
                    break

        risk_f = 0.0
        social_f = 0.0
        claim = action.get("claim")
        cdef = self.scenario.claims.get(claim) if claim else None
        asserted_prop = cdef.proposition if cdef else None
        for g in maintenance:
            for leaf in all_leaves(g.failure_condition):
                # a belief-threshold maintenance failure is endangered
                # by my LEARNING the proposition (my belief is what
                # the condition reads) — i.e. by asking about it or
                # hunting its evidence object — not by my asserting
                # what I already think (measured: the assert version
                # priced the merchant out of ever answering questions
                # about the mayor, breaking the testimony chain)
                if leaf.get("type") == "belief_threshold":
                    if verb == "ask" and asserted_prop \
                            and leaf.get("belief") == asserted_prop:
                        risk_f += g.priority_current * 0.6
                    if verb == "search" and cand["obj"]:
                        decl = self.scenario.objects.get(cand["obj"])
                        if decl and (decl.fact_belief == leaf.get("belief")
                                     or any(r.belief == leaf.get("belief")
                                            for r in decl.reveals)):
                            risk_f += g.priority_current * 0.6
        target = action.get("target")
        if verb == "confront" and target \
                and self._trust(view, target) < 0.4:
            risk_f += P.get("caution", 0.5) * 0.3
        if verb in ("speak", "ask", "confront") and cdef is not None \
                and cdef.stance == "assert" and not cdef.basis_beliefs():
            # an unevidenced assertion: honesty prices it, scaled by
            # how little the hearer trusts me anyway (design 3.6 —
            # the honesty trait's read point)
            social_f += P.get("honesty", 0.5) * 0.6
        if verb in ("speak", "confront") and cdef is not None and target:
            # first-hand disclosure: the claim rests on knowledge I
            # hold from direct experience (its declared speaker basis,
            # or the proposition itself); volunteering it to someone
            # below my disclosure threshold is priced like answering
            # a question with it — private knowledge is not given
            # away to the untrusted
            first_hand = False
            for key in cdef.basis_beliefs() or ([asserted_prop]
                                                if asserted_prop else []):
                held = view.beliefs.get(key)
                if held is not None and held.kind == "experienced":
                    first_hand = True
            if first_hand:
                # V0.2-C C2 (design 3.3): the coefficient differs
                # by communicative kind — answering an addressed
                # question carries the lower ANSWER coefficient
                # (social obligation), everything else keeps the
                # volunteering coefficient 1.6.
                coef = ANSWER_DISCLOSURE_COEF \
                    if cand.get("comm_kind") == "answer" else 1.6
                social_f += max(0.0, self.config.disclosure_threshold
                                - self._trust(view, target)) * coef
        if cand["source"] == "social" and target:
            trust_asker = self._trust(view, target)
            threshold = self.config.disclosure_threshold
            prop = asserted_prop
            if verb == "speak" and claim == self.scenario.deny_claim():
                social_f += max(0.0, trust_asker - threshold) * 0.8
                if prop is None:
                    # a denial about something I actually know: the
                    # honesty cost of lying (the denied proposition is
                    # the asker's claim's proposition — priced via the
                    # pending question, see _social_candidates)
                    known = cand.get("known_prop")
                    if known and self._conf(view, known) >= 0.9:
                        social_f += P.get("honesty", 0.5) * 0.4
            elif verb == "refuse":
                social_f += 0.1
            # (answering a question with first-hand knowledge is
            # priced by the uniform basis-disclosure cost below, not
            # by a response-specific term)

        # a pending question ADDRESSED TO ME is a conversational
        # obligation: its responses carry a fixed commitment bonus,
        # the social analogue of plan consistency — without it an
        # agent's own goal pitches would routinely shout down the
        # person it is talking to (measured in the first B1 runs:
        # questions were asked and simply never answered)
        # V0.2-C C2 (design 3.3): the bonus grows with the wait —
        # a question left sitting for ticks is a stronger claim on
        # the conversation than one just asked (+0.1 per tick
        # waited, capped at +0.3).
        obligation_f = 0.0
        if cand.get("obligation"):
            obligation_f = 0.55 + min(
                0.3, 0.1 * max(0, cand.get("obligation_waited", 0)))

        sig = _sig(action)
        rep = counts.get(sig, 0) * (REPETITION_HEAVY
                                    if verb in ("speak", "ask", "confront",
                                                "show", "search")
                                    else REPETITION_LIGHT)
        total = (base + W_GOAL * goal_f + W_INFO * info_f
                 + W_PERSONALITY * pers_f + W_EMOTION * emotion_f
                 + PLAN_BONUS * plan_f + obligation_f - W_RISK * risk_f
                 - W_SOCIAL * social_f - rep)
        return {"total": total, "factors": {
            "base": round(base, 3), "goal": round(goal_f, 3),
            "info": round(info_f, 3), "personality": round(pers_f, 3),
            "emotion": round(emotion_f, 3), "plan": round(plan_f, 3),
            "obligation": round(obligation_f, 3),
            "risk": round(risk_f, 3), "social": round(social_f, 3),
            "repetition": round(rep, 3)}}

    def _pool(self, context: DecisionContext) -> list:
        """The candidate pool (V0.2-C C1): the active goals PLUS
        the scheduler's pursuit goal when it is currently paused."""
        active = [g for g in context.active_goals if g.status == "active"]
        pursuit_goal = getattr(context, "pursuit_goal", None)
        if pursuit_goal is not None \
                and pursuit_goal.status in ("active", "paused") \
                and pursuit_goal.key not in {g.key for g in active}:
            active.append(pursuit_goal)
        return active

    # ---------------- candidate sources ----------------
    def _social_candidates(self, cands, context: DecisionContext,
                           view: SubjectiveView, counts: dict) -> None:
        """V0.2-C C2: the communication family now comes from the
        dedicated generator (v02/comm_candidates.py) — answers
        under the obligation, proactive asserts, clarifications,
        disclosures, denials, withholds — and competes in this
        provider's one market. This method stays as the thin
        adapter the generator plugs into (and that unit fixtures
        call directly)."""
        queue = getattr(context, "pending_questions", None)
        if queue is None:
            pq = getattr(context, "pending_question", None)
            queue = ([{"asker": pq.get("from"),
                       "claim_key": pq.get("claim"),
                       "proposition": None,
                       "tick_asked": pq.get("tick", context.tick),
                       "addressed_to_me": pq.get("addressed_to_me", True)}]
                     if pq else [])
        result = generate_comm_candidates(
            view=view, scenario=self.scenario, config=self.config,
            goals=self._pool(context), pending_questions=queue,
            colocated=self._colocated(context), counts=counts,
            tick=context.tick,
            line_fn=lambda claim, c: self._line(claim, c),
            assert_claim_for=lambda prop: self._assert_claim_for(view, prop))
        self._last_vetoed = result["vetoed"]
        cands.extend(result["candidates"])

    def _plan_candidates(self, cands, context: DecisionContext,
                         view: SubjectiveView, counts: dict) -> None:
        plan_step = getattr(context, "plan_step", None)
        if not plan_step:
            return
        plan = context.current_plan
        if plan is not None and plan.status == "suspended":
            # V0.2-C C1: a suspended plan generates no step
            # candidates — it waits for its reactivation condition;
            # the agent's live goals drive behaviour meanwhile.
            return
        goal_key = plan_step.get("goal_id") or (
            plan.goal if plan and plan.goal in
            {g.key for g in context.active_goals} else None)
        condition = plan_step.get("success_condition")
        if condition:
            sat, _ = evaluate(condition, view)
            if sat >= 1.0:
                return   # the step is done; nothing to generate for it
            for leaf in flatten_leaves(condition):
                lsat, _ = evaluate(leaf, view)
                if lsat < 1.0:
                    self._leaf_candidates(
                        cands, leaf, view, context, source="plan",
                        goal_key=goal_key, counts=counts)
        for tmpl in plan_step.get("action_candidates") or []:
            self._instantiate_template(cands, tmpl, context, view, counts,
                                       goal_key=goal_key, condition=condition)

    def _instantiate_template(self, cands, tmpl: dict,
                              context: DecisionContext,
                              view: SubjectiveView, counts: dict,
                              goal_key=None, condition: dict | None = None) -> None:
        verb = tmpl.get("type")
        colocated = self._colocated(context)
        if verb in ("speak", "ask", "confront"):
            claim = tmpl.get("claim")
            if not claim and condition:
                # a bare speech template takes its claim from the
                # step's own success condition: ask -> the inquire
                # class for the step's proposition; speak/confront ->
                # its assert class (only when I may assert it). A
                # template with no resolvable claim is not emitted —
                # claimless speech is noise, not a plan step.
                for leaf in flatten_leaves(condition):
                    if leaf.get("type") != "belief_threshold":
                        continue
                    prop = leaf["belief"]
                    if verb == "ask":
                        claim = self.scenario.claim_for_proposition(
                            prop, "inquire")
                    else:
                        cand_claim = self._assert_claim_for(
                            view, prop,
                            floor=CONFRONT_MIN_BASIS if verb == "confront"
                            else 0.0)
                        if cand_claim:
                            # a confrontation additionally requires
                            # that I actually HOLD the proposition at
                            # confront strength — basis-free claims
                            # (a sales pitch) are not confrontations
                            if verb != "confront" or self._conf(view, prop) \
                                    >= CONFRONT_MIN_BASIS:
                                claim = cand_claim
                    if claim:
                        break
            if not claim:
                return
            targets = [tmpl["target"]] if tmpl.get("target") else colocated
            for target in targets:
                self._speech(cands, verb, target, claim, source="plan",
                             goal_key=goal_key, counts=counts)
        elif verb == "search":
            place = tmpl.get("target_location") or context.location
            if place == context.location and self.scenario.searchable_at(place):
                self._cand(cands, {"type": "search",
                                   "target_location": place},
                           source="plan", goal_key=goal_key,
                           dest_place=place)
            else:
                # route toward the step's object, if the step's
                # condition names one (directly, or via its belief's
                # fact object)
                obj = tmpl.get("object")
                if obj is None and condition:
                    for leaf in flatten_leaves(condition):
                        if leaf.get("type") in ("object_possession",
                                                "object_found",
                                                "object_at_location"):
                            obj = leaf["object"]
                            break
                        if leaf.get("type") == "belief_threshold":
                            obj = self.scenario.object_for_fact_belief(
                                leaf["belief"])
                            if obj:
                                break
                if obj is not None:
                    self._search_route(cands, view, context, obj,
                                       source="plan", goal_key=goal_key)
                elif place != context.location:
                    hop = self._next_hop(context, {place})
                    if hop:
                        self._cand(cands, {"type": "move",
                                           "target_location": hop},
                                   source="plan", goal_key=goal_key)
        elif verb == "move":
            dest = tmpl.get("target_location")
            if dest and dest != context.location:
                hop = self._next_hop(context, {dest})
                if hop:
                    self._cand(cands, {"type": "move",
                                       "target_location": hop},
                               source="plan", goal_key=goal_key)
            else:
                for dest2 in sorted(self._adjacency(context).get(
                        context.location, [])):
                    self._cand(cands, {"type": "move",
                                       "target_location": dest2},
                               source="plan", goal_key=goal_key)
        elif verb == "show":
            obj = tmpl.get("object")
            objs = [obj] if obj else sorted(view.holdings)
            for o in objs:
                for target in colocated:
                    self._cand(cands, {"type": "show", "object": o,
                                       "target": target},
                               source="plan", goal_key=goal_key, obj=o)
        elif verb in ("observe", "listen", "work", "wait", "refuse"):
            action = {"type": verb}
            if tmpl.get("target"):
                action["target"] = tmpl["target"]
            if verb == "work" and self.config.routine.get("topic"):
                action["topic"] = self.config.routine["topic"]
            self._cand(cands, action, source="plan", goal_key=goal_key)

    # ---------------- main entry ----------------
    async def generate_decision(self, context: DecisionContext) -> ProviderResult:
        view = self._view(context)
        counts = dict(getattr(context, "action_counts", {}) or {})
        cands: list[dict] = []

        # V0.2-C C1: the candidate pool is the active goals PLUS the
        # scheduler's pursuit goal when it is currently paused (a
        # paused pursuit resumes by being served, so it must be able
        # to generate candidates). Selection: the scheduler's
        # pursuit when the context carries one; otherwise the pre-C
        # priority-first rule, unchanged (standalone contexts in
        # unit tests carry no schedule and behave exactly as B).
        active = self._pool(context)
        active.sort(key=lambda g: (-g.priority_current, g.key))
        sched_id = getattr(context, "scheduled_pursuit_goal_id", None)
        selected = None
        if sched_id:
            selected = next((g for g in active if g.key == sched_id), None)
        if selected is None:
            selected = active[0] if active else None
        eligible_ids = getattr(context, "eligible_goal_ids", None)
        if sched_id and eligible_ids is not None:
            rest = [g for g in active
                    if g.key in eligible_ids
                    and (selected is None or g.key != selected.key)]
            second = rest[0] if rest else None
        else:
            second = active[1] if len(active) > 1 else None
        maintenance = [g for g in active if g.kind == "maintenance"]

        # 1. goal-driven candidates from unsatisfied success leaves
        for g in active:
            if g.kind != "achievement" or not g.success_condition:
                continue
            sat, _ = evaluate(g.success_condition, view)
            if sat >= 1.0:
                continue
            for leaf in all_leaves(g.success_condition):
                lsat, _ = evaluate(leaf, view)
                if lsat < 1.0:
                    self._leaf_candidates(cands, leaf, view, context,
                                          source="goal", goal_key=g.key,
                                          counts=counts)
        # 2. plan-step candidates
        self._plan_candidates(cands, context, view, counts)
        # 2b. seek-interlocutor: speech templates are useless with
        # nobody in reach. If an unsatisfied belief leaf wants
        # testimony and nobody is colocated, move toward where
        # others were last seen — or, knowing nothing, toward an
        # adjacent place (exploration in service of the same leaf).
        if not self._colocated(context):
            wants_speech = any(
                leaf.get("type") in ("belief_threshold", "event_occurred")
                and evaluate(leaf, view)[0] < 1.0
                for g in active if g.kind == "achievement"
                and g.success_condition
                for leaf in all_leaves(g.success_condition))
            if wants_speech:
                seen: dict[str, str] = {}
                for ev in view.perceived_events:
                    if ev.get("actor") and ev.get("actor") != context.character_id:
                        if ev.get("type") == "move" and ev.get("to"):
                            seen[ev["actor"]] = ev["to"]
                        elif ev.get("location"):
                            seen[ev["actor"]] = ev["location"]
                targets = {loc for loc in seen.values()
                           if loc != context.location}
                if not targets:
                    targets = set(self._adjacency(context).get(
                        context.location, []))
                for place in sorted(targets):
                    hop = self._next_hop(context, {place}) \
                        if place not in self._adjacency(context).get(
                            context.location, []) else place
                    if hop:
                        self._cand(cands, {"type": "move",
                                           "target_location": hop},
                                   source="goal",
                                   goal_key=selected.key if selected else None)
        # 3. social candidates
        self._social_candidates(cands, context, view, counts)
        # 3a. V0.2-C fix round: DUTY SEEKING. A disclosure duty in
        # force whose named recipient is absent and has not yet
        # been truly told claims my movement the way a pending
        # question claims my speech: a move toward the recipient's
        # last-seen location — derived from my perception history
        # only (conditions.last_seen_location), routed by the same
        # BFS as every other move. Its information value is the
        # duty's own urgency (the price the eventual disclose
        # candidate carries), and it bears the obligation bonus,
        # waited since I came to hold the reportable knowledge.
        # Duties are character DATA; a cast without duties data
        # generates nothing here. Co-location semantics are the
        # world's and are not touched: crossing paths in one tick
        # still does not make a meeting.
        for item in undelivered_duties(view, self.config, self.scenario,
                                       self._colocated(context)):
            duty, target = item["duty"], item["target"]
            seen = last_seen_location(view, target)
            if not seen or seen == context.location:
                continue
            hop = self._next_hop(context, {seen})
            if not hop:
                continue
            since = proposition_held_since(view, duty.get("proposition"))
            waited = max(0, context.tick - since) \
                if since is not None else 0
            cands.append({
                "action": {"type": "move", "target_location": hop},
                "source": "social", "goal_key": None, "leaf": None,
                "prop": duty.get("proposition"), "obj": None,
                "dest_place": seen, "comm_kind": "disclose",
                "obligation": True, "obligation_waited": waited,
                "info_override": URGENCY_IV.get(duty.get("urgency"), 0.7)})
        # 3b. V0.2-C C3 (design 4.2.2): BASELINE EXPLORATION.
        # Only on a genuinely idle tick — no goal-leaf or plan
        # candidate was generated and no addressed question is
        # waiting — an unsearched searchable place is worth a
        # look: IV = curiosity x 0.2 (the relevance floor), an
        # order below any goal-driven action, so it surfaces only
        # when nothing with a purpose is on the table. It is
        # deliberately NOT aimed at any object: aiming it would
        # be plot knowledge, not exploration.
        if not any(c["source"] in ("goal", "plan") for c in cands):
            queue = getattr(context, "pending_questions", None) or []
            colocated_now = self._colocated(context)
            obligation_active = any(
                e.get("addressed_to_me", True)
                and e.get("asker") in colocated_now for e in queue)
            if not obligation_active:
                curiosity = self.config.personality.get("curiosity", 0.5)
                for place in self.scenario.searchable_locations():
                    if place in view.searched_places:
                        continue
                    if place == context.location:
                        cands.append({
                            "action": {"type": "search",
                                       "target_location": place},
                            "source": "baseline", "goal_key": None,
                            "leaf": None, "prop": None, "obj": None,
                            "dest_place": place,
                            "info_override": curiosity * 0.2})
                    else:
                        hop = self._next_hop(context, {place})
                        if hop:
                            cands.append({
                                "action": {"type": "move",
                                           "target_location": hop},
                                "source": "baseline", "goal_key": None,
                                "leaf": None, "prop": None, "obj": None,
                                "dest_place": place,
                                "info_override": 0.8 * curiosity * 0.2})
        # 4. baseline candidates
        for verb in ("observe", "listen", "wait"):
            self._cand(cands, {"type": verb}, source="baseline")
        work = {"type": "work"}
        if self.config.routine.get("topic"):
            work["topic"] = self.config.routine["topic"]
        self._cand(cands, work, source="baseline")

        # Only what the context actually offered may be chosen (the
        # registry's capability set) — the generator never emits an
        # action the environment does not afford here.
        offered = [c for c in cands
                   if action_is_offered(c["action"], context.available_actions)]
        # de-duplicate by signature, keeping the best source priority
        by_sig: dict[str, dict] = {}
        for c in offered:
            sig = _sig(c["action"])
            if sig not in by_sig:
                by_sig[sig] = c
        uniq = [by_sig[s] for s in sorted(by_sig)]
        # V0.2-C C2 (design 3.6): candidates from the other sources
        # get their census kind by their utterance's stance
        # (generator candidates arrive pre-tagged).
        for c in uniq:
            if "comm_kind" not in c:
                c["comm_kind"] = None
                claim = c["action"].get("claim")
                cdef = self.scenario.claims.get(claim) if claim else None
                if c["action"]["type"] == "ask":
                    c["comm_kind"] = "ask"
                elif c["action"]["type"] in ("speak", "confront") \
                        and cdef is not None and cdef.stance == "assert":
                    c["comm_kind"] = "assert"
                elif c["action"]["type"] == "refuse":
                    c["comm_kind"] = "withhold"

        plan_step = getattr(context, "plan_step", None)
        scored = []
        for c in uniq:
            res = self._score(c, context, view,
                              selected.key if selected else None,
                              second.key if second else None,
                              maintenance, plan_step, counts)
            jitter = self.rng.random() * JITTER_MAX
            scored.append({**c, "total": res["total"] + jitter,
                           "factors": res["factors"], "jitter": round(jitter, 4),
                           "sig": _sig(c["action"])})
        scored.sort(key=lambda c: (-c["total"], c["sig"]))
        best = scored[0]
        action = dict(best["action"])
        content = action.pop("content", None)

        # Plan synthesis targets the highest-priority achievement
        # goal with a success condition: a maintenance goal can top
        # the priority list (it has no success condition by
        # construction — there is nothing to synthesize TOWARD), and
        # letting it starve synthesis would leave every achievement
        # goal planless whenever a maintenance goal outranks it.
        synth_goal = selected
        if synth_goal is None or not synth_goal.success_condition:
            synth_goal = next(
                (g for g in active
                 if g.kind == "achievement" and g.success_condition),
                None)
        plan_update = self._maybe_synthesize(context, view, synth_goal)
        if plan_update is None:
            plan_update = PlanUpdate(status="continue")
        # V0.2-B B3: a BLOCKED plan is exited through the Plan
        # Manager's recovery machine, never through advance. When
        # the blocked plan's goal can be re-synthesized, attach the
        # fresh steps as a replace — the runtime reads a replace on
        # a blocked plan as the replan recovery's material. When it
        # cannot, emit a bare continue and let the scheduled policy
        # default (retry/skip/...) execute instead.
        plan_now = context.current_plan
        if plan_now is not None and plan_now.status == "blocked":
            goal_key = plan_now.goal_id or (selected.key if selected
                                            else None)
            goal_obj = next((g for g in context.active_goals
                             if g.key == goal_key), None)
            if goal_obj is not None and goal_obj.success_condition:
                steps = self.synthesize_steps(goal_obj, view)
                if steps:
                    plan_update = PlanUpdate(
                        status="replace", reason="replan after blocked",
                        new_goal=goal_obj.key, new_steps=steps,
                        goal_id=goal_obj.key)

        decision = AgentDecision(
            intent=_sig(action),
            target=action.get("target"),
            action=ActionSpec(type=action["type"], target=action.get("target"),
                              target_location=action.get("target_location"),
                              object=action.get("object"), content=content,
                              claim=action.get("claim"),
                              topic=action.get("topic")),
            reasoning_summary=(
                f"[rule-generic] source={best['source']} "
                f"goal={best['goal_key']} total={best['total']:.3f} "
                f"factors={best['factors']}"),
            expected_outcome="situation_develops",
            confidence=round(min(0.95, max(0.05, best["total"])), 2),
            plan_update=plan_update,
            selected_goal_id=best["goal_key"],
        )
        candidate_scores = {
            "chosen": best["sig"],
            "candidates": [
                {"action_sig": c["sig"], "total": round(c["total"], 4),
                 "factors": c["factors"], "jitter": c["jitter"],
                 "source": c["source"], "goal_key": c["goal_key"]}
                for c in scored[:5]],
        }
        # V0.2-C C2 (design 3.6): the candidate CENSUS over ALL
        # scored candidates — computed here, after scoring and
        # before the top-5 truncation above, so downstream metrics
        # see generation and selection, not just the shortlist.
        from collections import Counter
        census = {
            "n_generated": len(scored),
            "by_source": dict(sorted(Counter(
                c["source"] for c in scored).items())),
            "by_comm_kind": dict(sorted(Counter(
                c["comm_kind"] for c in scored
                if c.get("comm_kind")).items())),
            "assert_generated": sum(
                1 for c in scored if c.get("comm_kind") == "assert"),
            "answer_generated": sum(
                1 for c in scored if c.get("comm_kind") == "answer"),
            "search_generated": sum(
                1 for c in scored if c["action"]["type"] == "search"),
            "chosen_comm_kind": best.get("comm_kind"),
            "vetoed": list(getattr(self, "_last_vetoed", []) or []),
        }
        return ProviderResult(decision=decision, provider=self.name,
                              model=self.model,
                              raw_output=decision.model_dump_json(),
                              candidate_scores=candidate_scores,
                              census=census)
