"""Plan Manager (Phase C, design section 4.4).

Owns the full plan lifecycle on a CharacterState:

  set_plan        — create a plan (interpretation-driven, as in Phase A);
                    replacing an existing plan is logged as a transition
  reconcile       — called once per tick before the context is built:
                    commitment decays by DECAY_PER_TICK for every tick
                    without counted progress (floored at MIN_COMMITMENT),
                    which makes "the agent is losing faith in this plan"
                    a measurable quantity instead of a vibe
  apply_update    — the provider's plan_update, reason-gated exactly as
                    Phase A: abandon/replace without a reason are
                    rejected (and the rejection is recorded both in the
                    plan's interrupt_log and as a transition); with a
                    reason they take effect
  advance_from_result — the plan-layer upgrade's entry point: when the
                    execution result of the action chosen for the
                    current step comes back and the runtime judges the
                    step fulfilled (shared predicates, plan_steps.py),
                    the agent advances through this — same _advance
                    machinery, reason "result_driven:<step>"
  current_step_view — the "current step" injected into the decision
                    context (design 4.4: Context 注入当前步）

Step counting (the V0.1 oscillation fix): an `advance` counts only when
its action differs from the last COUNTED advance's action signature.
Repeating the same action — classically, moving toward the same target
again and again — is logged as `advance_dedup` and counts nothing, and
because it is not progress it does not stop commitment decay either.
A counted advance restores a little commitment (+PROGRESS_BONUS).

Every mutation appends a transition dict to self.transitions; the
agent drains the buffer and the scheduler persists the transitions
(plan_transitions table), so plan migration is auditable per decision.
Nothing here touches another agent or the world.
"""
from __future__ import annotations

from .protocol import PlanState, PlanUpdate

_PLAN_SEQ = [0]


def plan_seq() -> int:
    """Current plan-id counter value. Checkpointed (checkpoint.py) so a
    resumed process keeps issuing fresh, non-colliding plan ids exactly
    as the uninterrupted process would have."""
    return _PLAN_SEQ[0]


def restore_plan_seq(value: int) -> None:
    _PLAN_SEQ[0] = int(value)

DECAY_PER_TICK = 0.02
MIN_COMMITMENT = 0.1
MAX_COMMITMENT = 0.95
PROGRESS_BONUS = 0.05

# V0.2-A Phase 3: a plan whose current step is genuinely attempted
# and fails this many times in a row becomes "blocked"; and an
# interpretation-driven replacement may not displace a plan whose
# commitment is at or above COMMITMENT_GATE unless the triggering
# information is critical (first-hand / directly revealing).
BLOCK_THRESHOLD = 2
COMMITMENT_GATE = 0.7


class PlanManager:
    def __init__(self):
        self.transitions: list[dict] = []
        # V0.2-A Phase 3: goal lifecycle changes driven by plan events
        # (see _sync_goal); drained by the agent exactly like plan
        # transitions and persisted to the goal_changes table.
        self.goal_changes: list[dict] = []

    # ---------------- internal ----------------
    @staticmethod
    def _snap(plan: PlanState | None) -> dict | None:
        return plan.model_dump() if plan else None

    def _log(self, state, tick: int, kind: str, plan_id: str | None,
             reason: str | None = None, before=None, after=None) -> None:
        self.transitions.append(
            {"tick": tick, "character_id": state.character_id,
             "plan_id": plan_id, "kind": kind, "reason": reason,
             "before": before, "after": after})

    # ---------------- goal lifecycle (V0.2-A Phase 3) ----------------
    # Goals are runtime objects, but their lifecycle is DETERMINISTIC:
    # it is driven only by plan events for a plan whose goal label
    # equals the goal's key (plan labels are otherwise a free
    # namespace — "press_for_evidence" is a plan label, not a goal).
    #   plan set for goal G        -> G active (a paused G resumes);
    #                                 the previously pursued different
    #                                 goal is paused (single-plan
    #                                 runtime: its pursuit is suspended)
    #   counted step advance       -> G.progress tracks the plan's
    #                                 step fraction (monotonic)
    #   plan completed             -> G completed, progress 1.0
    #   plan abandoned (provider,
    #   with reason)               -> G abandoned; setting a new plan
    #                                 for G reactivates it
    # Every change is recorded in self.goal_changes with before/after
    # snapshots. Providers never touch Goal objects directly.
    @staticmethod
    def _goal(state, key: str):
        for g in state.goals:
            if g.key == key:
                return g
        return None

    def _record_goal(self, state, tick: int, goal, kind: str,
                     reason: str | None, before: dict | None) -> None:
        self.goal_changes.append(
            {"tick": tick, "character_id": state.character_id,
             "goal_key": goal.key, "kind": kind, "reason": reason,
             "before": before, "after": goal.model_dump()})

    def drain_goal_changes(self) -> list[dict]:
        changes, self.goal_changes = self.goal_changes, []
        return changes

    def _sync_goal_on_set(self, state, tick: int,
                          old_plan: PlanState | None, new_goal: str) -> None:
        if old_plan is not None and old_plan.goal != new_goal:
            prev = self._goal(state, old_plan.goal)
            if prev is not None and prev.status == "active":
                before = prev.model_dump()
                prev.status = "paused"
                self._record_goal(state, tick, prev, "goal_paused",
                                  f"plan switched to {new_goal}", before)
        goal = self._goal(state, new_goal)
        if goal is not None and goal.status in ("paused", "abandoned"):
            before = goal.model_dump()
            goal.status = "active"
            self._record_goal(state, tick, goal, "goal_reactivated",
                              "new plan set for this goal", before)

    def _sync_goal_on_progress(self, state, tick: int, plan: PlanState) -> None:
        goal = self._goal(state, plan.goal)
        if goal is None or not plan.steps:
            return
        if plan.status == "completed" \
                or plan.current_step >= len(plan.steps):
            if goal.status != "completed":
                before = goal.model_dump()
                goal.progress = 1.0
                goal.status = "completed"
                self._record_goal(state, tick, goal, "goal_completed",
                                  "linked plan completed", before)
            return
        new_progress = round(plan.current_step / len(plan.steps), 3)
        if new_progress > goal.progress:
            before = goal.model_dump()
            goal.progress = new_progress
            self._record_goal(state, tick, goal, "goal_progress",
                              f"plan step {plan.current_step}/{len(plan.steps)}",
                              before)

    def _sync_goal_on_abandon(self, state, tick: int, plan: PlanState,
                              reason: str | None) -> None:
        goal = self._goal(state, plan.goal)
        if goal is not None and goal.status == "active":
            before = goal.model_dump()
            goal.status = "abandoned"
            self._record_goal(state, tick, goal, "goal_abandoned",
                              reason, before)

    # ---------------- creation ----------------
    def set_plan(self, state, goal: str, steps: list[str], tick: int,
                 reason: str | None = None) -> PlanState:
        old = state.current_plan
        _PLAN_SEQ[0] += 1
        plan = PlanState(plan_id=f"plan_{_PLAN_SEQ[0]:03d}", goal=goal,
                         steps=list(steps), current_step=0, commitment=0.8,
                         status="active", last_progress_tick=tick,
                         commitment_updated_tick=tick)
        state.current_plan = plan
        self._log(state, tick, "replace" if old else "set", plan.plan_id,
                  reason=reason, before=self._snap(old), after=self._snap(plan))
        self._sync_goal_on_set(state, tick, old, goal)
        return plan

    def note_blocked_replacement(self, state, tick: int,
                                 attempted_goal: str) -> None:
        """Audit record for the interpretation-side commitment gate
        (agent._set_plan): a high-commitment plan was NOT displaced by
        non-critical information. The plan itself is untouched."""
        plan = state.current_plan
        if plan is not None:
            plan.interrupt_log.append(
                {"tick": tick, "kind": "replace_blocked_by_commitment",
                 "attempted_goal": attempted_goal})
        self._log(state, tick, "replace_blocked_by_commitment",
                  plan.plan_id if plan else None,
                  reason=f"commitment {plan.commitment:.2f} >= gate; "
                         f"{attempted_goal} not critical",
                  before=self._snap(plan), after=self._snap(plan))

    # ---------------- step failure / blocked (V0.2-A Phase 3) ----------
    def note_step_failure(self, state, tick: int, step: str) -> str:
        """The action chosen for the plan's current step was judged
        step-consistent (the agent genuinely tried the step) but its
        result did not fulfil the step. Consecutive failures reaching
        BLOCK_THRESHOLD block the plan: it stops decaying, result-
        driven advancement no longer applies, and only a deliberate
        provider advance / replace / abandon moves it again."""
        plan = state.current_plan
        if plan is None or plan.status != "active":
            return "no_active_plan"
        plan.step_failures += 1
        if plan.step_failures >= BLOCK_THRESHOLD:
            before = self._snap(plan)
            plan.status = "blocked"
            self._log(state, tick, "blocked", plan.plan_id,
                      reason=f"step {step!r} failed "
                             f"{plan.step_failures} times in a row",
                      before=before, after=self._snap(plan))
            return "blocked"
        self._log(state, tick, "step_failure", plan.plan_id,
                  reason=f"step {step!r} attempt "
                         f"{plan.step_failures} did not fulfil",
                  before=self._snap(plan), after=self._snap(plan))
        return "step_failure"

    # ---------------- context ----------------
    def current_step_view(self, state) -> dict | None:
        plan = state.current_plan
        if plan is None:
            return None
        step = plan.steps[plan.current_step] \
            if plan.current_step < len(plan.steps) else None
        return {"plan_id": plan.plan_id, "goal": plan.goal,
                "step_index": plan.current_step, "step": step,
                "steps_total": len(plan.steps), "commitment": plan.commitment,
                "status": plan.status}

    # ---------------- per-tick housekeeping ----------------
    def reconcile(self, state, tick: int) -> None:
        plan = state.current_plan
        if plan is None or plan.status != "active":
            return
        if tick > plan.commitment_updated_tick:
            before = plan.commitment
            plan.commitment = round(max(
                MIN_COMMITMENT,
                plan.commitment - DECAY_PER_TICK * (tick - plan.commitment_updated_tick)), 3)
            plan.commitment_updated_tick = tick
            if plan.commitment != before:
                self._log(state, tick, "commitment_decay", plan.plan_id,
                          before=before, after=plan.commitment)

    # ---------------- provider plan_update ----------------
    def apply_update(self, state, pu: PlanUpdate, action_sig: str,
                     tick: int) -> str:
        """Returns the same note strings the Phase A runtime returned."""
        plan = state.current_plan
        if pu.status in ("abandon", "replace") and not pu.reason:
            if plan:
                plan.interrupt_log.append(
                    {"tick": tick, "kind": f"{pu.status}_rejected_no_reason"})
            self._log(state, tick, f"{pu.status}_rejected_no_reason",
                      plan.plan_id if plan else None,
                      before=self._snap(plan), after=self._snap(plan))
            return f"{pu.status}_rejected_no_reason"

        if pu.status == "abandon" and plan:
            plan.status = "abandoned"
            plan.interrupt_log.append({"tick": tick, "kind": "abandon",
                                       "reason": pu.reason})
            state.current_plan = None
            self._log(state, tick, "abandon", plan.plan_id, reason=pu.reason,
                      before=self._snap(plan), after=None)
            self._sync_goal_on_abandon(state, tick, plan, pu.reason)
            return "abandoned"

        if pu.status == "replace" and pu.new_steps:
            self.set_plan(state, pu.new_goal or (plan.goal if plan else "new_goal"),
                          pu.new_steps, tick, reason=pu.reason)
            return "replaced"

        if pu.status == "advance" and plan and plan.status in ("active", "blocked"):
            if plan.status == "blocked":
                # A deliberate provider advance is the documented way
                # out of a blocked plan: un-block, clear the failure
                # count, then advance as usual.
                before = self._snap(plan)
                plan.status = "active"
                plan.step_failures = 0
                self._log(state, tick, "unblocked", plan.plan_id,
                          reason="provider advance on blocked plan",
                          before=before, after=self._snap(plan))
            return self._advance(state, plan, action_sig, tick)

        self._log(state, tick, pu.status, plan.plan_id if plan else None,
                  before=self._snap(plan), after=self._snap(plan))
        return pu.status

    def advance_from_result(self, state, action_sig: str, tick: int,
                            step: str) -> str:
        """Result-driven step advancement (plan-layer upgrade).

        The agent has received the execution result of the action it
        chose for the plan's current step, and the runtime — not the
        provider — judged that action to have fulfilled the step (see
        v02/plan_steps.py: step completion is a fact about the world,
        not a provider opinion). Delegates to the same _advance
        machinery as a provider 'advance' — same signature dedup, same
        commitment bonus, same completion — so an explicit provider
        advance and a result-driven one can never double-count the
        same action. The transition's reason records the provenance
        and the fulfilled step."""
        plan = state.current_plan
        if plan is None or plan.status != "active":
            return "no_active_plan"
        return self._advance(state, plan, action_sig, tick,
                             reason=f"result_driven:{step}")

    def _advance(self, state, plan: PlanState, action_sig: str,
                 tick: int, reason: str | None = None) -> str:
        step_idx = plan.current_step
        step_name = plan.steps[step_idx] if step_idx < len(plan.steps) else None
        last_counted = next((h for h in reversed(plan.step_history)
                             if h["counted"]), None)
        if last_counted is not None \
                and last_counted["action_sig"] == action_sig:
            plan.step_history.append(
                {"tick": tick, "step_index": step_idx, "step": step_name,
                 "action_sig": action_sig, "counted": False})
            self._log(state, tick, "advance_dedup", plan.plan_id,
                      reason=reason,
                      before=self._snap(plan), after=self._snap(plan))
            return "advance_dedup"
        before = self._snap(plan)
        plan.step_history.append(
            {"tick": tick, "step_index": step_idx, "step": step_name,
             "action_sig": action_sig, "counted": True})
        plan.current_step = min(step_idx + 1, len(plan.steps))
        plan.step_failures = 0
        plan.last_progress_tick = tick
        plan.commitment = round(min(MAX_COMMITMENT,
                                    plan.commitment + PROGRESS_BONUS), 3)
        plan.commitment_updated_tick = tick
        self._sync_goal_on_progress(state, tick, plan)
        if plan.current_step >= len(plan.steps):
            plan.status = "completed"
            state.current_plan = None
            self._log(state, tick, "advance", plan.plan_id, reason=reason,
                      before=before, after=self._snap(plan))
        else:
            self._log(state, tick, "advance", plan.plan_id, reason=reason,
                      before=before, after=self._snap(plan))
        return "advanced"
