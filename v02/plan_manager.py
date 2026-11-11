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
  apply_step_evaluation — V0.2-B B3: the StepEvaluator's verdict on
                    the current step (fulfilled / counted failure /
                    undecidable), judged from the step's declared
                    conditions against the action's RESULT; fulfilment
                    advances through the same _advance machinery with
                    reason "result_driven:<step_id>", counted failures
                    fill the step's attempts and can enter blocked
  execute_recovery — V0.2-B B3: the blocked/suspended recovery state
                    machine (retry / replan / suspend / abandon /
                    skip + suspended reactivation), design 5.5
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
        # V0.2-B B2: kept as an always-empty compatibility buffer —
        # goal changes are produced by the GoalEvaluator and drained
        # by the agent itself, never by this manager.
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

    # ---------------- goal lifecycle ----------------
    # V0.2-B B2: the Plan Manager no longer writes goal state AT
    # ALL. The V0.2-A _sync_goal_* methods (plan events driving goal
    # status/progress, progress = current_step/len(steps)) are
    # deleted: goal state is owned by the GoalEvaluator
    # (v02/goal_engine.py), which judges declared CONDITIONS on the
    # agent's subjective state. A plan carries a stable goal_id link
    # (design 4.5); an unlinked plan touches no goal, ever.

    # ---------------- creation ----------------
    def set_plan(self, state, goal: str, steps: list, tick: int,
                 reason: str | None = None,
                 goal_id: str | None = None) -> PlanState:
        old = state.current_plan
        _PLAN_SEQ[0] += 1
        plan = PlanState(plan_id=f"plan_{_PLAN_SEQ[0]:03d}", goal=goal,
                         steps=list(steps), current_step=0, commitment=0.8,
                         status="active", last_progress_tick=tick,
                         commitment_updated_tick=tick, goal_id=goal_id)
        if plan.steps:
            plan.steps[0].status = "active"
        state.current_plan = plan
        self._log(state, tick, "replace" if old else "set", plan.plan_id,
                  reason=reason, before=self._snap(old), after=self._snap(plan))
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

    # ---------------- step evaluation / blocked / recovery (B3) ----
    def apply_step_evaluation(self, state, tick: int, step_id: str,
                              evaluation, action_sig: str) -> str:
        """Consume one StepEvaluation for the plan's CURRENT step
        (design 5.3/5.4). Fulfilment advances through the same
        _advance machinery as a provider advance (shared dedup);
        a counted failure increments the STEP's own attempts, and
        reaching the step's retry policy — or a terminal failure
        condition — enters blocked and schedules the policy's
        default recovery. Every evaluation is logged as a
        step_evaluated transition whose after-payload carries the
        full StepEvaluation and the step's attempts."""
        plan = state.current_plan
        if plan is None or plan.status != "active":
            return "no_active_plan"
        if plan.current_step >= len(plan.steps):
            return "no_current_step"
        step = plan.steps[plan.current_step]
        if step.step_id != step_id:
            return "stale_step"
        after = {**evaluation.to_dict(), "step_id": step.step_id,
                 "attempts": step.attempts}
        if evaluation.legacy:
            self._log(state, tick, "step_evaluated", plan.plan_id,
                      reason="legacy step: not condition-judged",
                      before=self._snap(plan), after=after)
            return "legacy"
        if evaluation.fulfilled:
            self._log(state, tick, "step_evaluated", plan.plan_id,
                      reason=evaluation.reason,
                      before=self._snap(plan), after=after)
            return self._advance(state, plan, action_sig, tick,
                                 reason=f"result_driven:{step.step_id}")
        if evaluation.failed:
            step.attempts += 1
            after["attempts"] = step.attempts
            self._log(state, tick, "step_evaluated", plan.plan_id,
                      reason=evaluation.reason,
                      before=self._snap(plan), after=after)
            if evaluation.terminal or \
                    step.attempts >= step.retry_policy.max_attempts:
                before = self._snap(plan)
                plan.status = "blocked"
                plan.blocked_info = {
                    "reason": evaluation.reason, "since_tick": tick,
                    "step_id": step.step_id, "attempts": step.attempts}
                plan.pending_recovery = {
                    "strategy": step.retry_policy.on_exhausted,
                    "source": "policy_default"}
                self._log(state, tick, "blocked", plan.plan_id,
                          reason=f"step {step.step_id!r}: "
                                 f"{evaluation.reason} "
                                 f"(attempts {step.attempts})",
                          before=before, after=self._snap(plan))
                return "blocked"
            return "step_failure"
        self._log(state, tick, "step_evaluated", plan.plan_id,
                  reason=evaluation.reason,
                  before=self._snap(plan), after=after)
        return "evaluated"

    def reset_attempts_on_environment_change(self, state, tick: int,
                                             refs: str) -> bool:
        """Design 5.4: a newly perceived event intersecting the
        current step condition's reference set resets the step's
        failure counter (the world the step was failing against has
        changed) and forces re-evaluation next cycle."""
        plan = state.current_plan
        if plan is None or plan.status != "active":
            return False
        if plan.current_step >= len(plan.steps):
            return False
        step = plan.steps[plan.current_step]
        if step.attempts <= 0:
            return False
        before = self._snap(plan)
        step.attempts = 0
        self._log(state, tick, "environment_reset", plan.plan_id,
                  reason=f"perceived event intersects {refs}",
                  before=before, after=self._snap(plan))
        return True

    def execute_recovery(self, state, tick: int, strategy: str,
                         reason: str | None = None, *,
                         source: str = "provider",
                         new_steps: list | None = None,
                         new_goal_label: str | None = None,
                         goal_id: str | None = None,
                         reactivate_when: dict | None = None) -> str:
        """The blocked/suspended recovery state machine (design
        5.5). Legal transitions only; an illegal proposal is logged
        (kind recovery_rejected / recovery_deferred) and changes
        nothing. Every executed recovery logs kind=recovery with
        the strategy and source in the reason."""
        plan = state.current_plan
        if plan is None:
            return "no_plan"
        # suspended reactivation arrives through this entry point
        # from the GoalEvaluator with strategy "suspend"
        if plan.status == "suspended":
            if source == "evaluator":
                before = self._snap(plan)
                plan.status = "active"
                plan.reactivate_when = None
                self._log(state, tick, "recovery", plan.plan_id,
                          reason="strategy=reactivate "
                                 "source=evaluator: "
                                 "reactivate_condition_met",
                          before=before, after=self._snap(plan))
                return "recovered_reactivate"
            return "not_blocked"
        if plan.status != "blocked":
            return "not_blocked"
        step = plan.steps[plan.current_step] \
            if plan.current_step < len(plan.steps) else None

        def reject(kind: str, why: str) -> str:
            self._log(state, tick, kind, plan.plan_id, reason=why,
                      before=self._snap(plan), after=self._snap(plan))
            return kind

        if strategy == "retry":
            if step is None:
                return reject("recovery_rejected", "retry: no current step")
            used = plan.retry_recoveries.get(step.step_id, 0)
            if used >= 2:
                return reject(
                    "recovery_rejected",
                    f"retry cap reached for step {step.step_id!r} "
                    f"({used} recoveries already)")
            before = self._snap(plan)
            plan.retry_recoveries[step.step_id] = used + 1
            step.attempts = 0
            plan.status = "active"
            plan.blocked_info = None
            plan.pending_recovery = None
            self._log(state, tick, "recovery", plan.plan_id,
                      reason=f"strategy=retry source={source}: "
                             f"{reason or ''}".rstrip(),
                      before=before, after=self._snap(plan))
            return "recovered_retry"
        if strategy == "skip":
            if step is None:
                return reject("recovery_rejected", "skip: no current step")
            before = self._snap(plan)
            step.status = "skipped"
            self._log(state, tick, "step_skipped", plan.plan_id,
                      reason=f"step {step.step_id!r} skipped by "
                             f"recovery (source={source})",
                      before=before, after=self._snap(plan))
            plan.blocked_info = None
            plan.pending_recovery = None
            nxt = next((i for i in range(plan.current_step + 1,
                                         len(plan.steps))
                        if plan.steps[i].status in ("pending", "active")),
                       None)
            if nxt is None:
                plan.status = "completed"
                skipped = sum(1 for s in plan.steps
                              if s.status == "skipped")
                state.current_plan = None
                self._log(state, tick, "recovery", plan.plan_id,
                          reason=f"strategy=skip source={source}: "
                                 f"{reason or ''}".rstrip(),
                          before=before, after=self._snap(plan))
                self._log(state, tick, "plan_completed", plan.plan_id,
                          reason=f"all steps fulfilled or skipped "
                                 f"(skipped={skipped})",
                          before=before, after=self._snap(plan))
            else:
                plan.current_step = nxt
                plan.steps[nxt].status = "active"
                plan.status = "active"
                self._log(state, tick, "recovery", plan.plan_id,
                          reason=f"strategy=skip source={source}: "
                                 f"{reason or ''}".rstrip(),
                          before=before, after=self._snap(plan))
            return "recovered_skip"
        if strategy == "suspend":
            cond = reactivate_when
            if cond is None and source == "policy_default" \
                    and step is not None:
                cond = step.success_condition
            if cond is None:
                return reject(
                    "recovery_rejected",
                    "suspend requires a reactivate_when condition")
            before = self._snap(plan)
            plan.status = "suspended"
            plan.reactivate_when = cond
            plan.pending_recovery = None
            self._log(state, tick, "recovery", plan.plan_id,
                      reason=f"strategy=suspend source={source}: "
                             f"{reason or ''}".rstrip(),
                      before=before, after=self._snap(plan))
            return "recovered_suspend"
        if strategy == "abandon":
            if not reason:
                return reject("recovery_rejected",
                              "abandon requires a reason")
            before = self._snap(plan)
            plan.status = "abandoned"
            plan.interrupt_log.append(
                {"tick": tick, "kind": "abandon", "reason": reason,
                 "via": "recovery"})
            plan.pending_recovery = None
            state.current_plan = None
            self._log(state, tick, "recovery", plan.plan_id,
                      reason=f"strategy=abandon source={source}: {reason}",
                      before=before, after=self._snap(plan))
            return "recovered_abandon"
        if strategy == "replan":
            if not new_steps:
                # legal but not executable yet: the replacement must
                # arrive in the same decision (design 5.5)
                return reject(
                    "recovery_deferred",
                    "replan needs replacement steps in the same "
                    "decision; none were supplied")
            before = self._snap(plan)
            old_id, old_goal_id = plan.plan_id, plan.goal_id
            plan.status = "abandoned"
            plan.interrupt_log.append(
                {"tick": tick, "kind": "abandon", "reason": "replanned"})
            state.current_plan = None
            self._log(state, tick, "abandon", old_id,
                      reason="replanned",
                      before=before, after=self._snap(plan))
            self._log(state, tick, "recovery", old_id,
                      reason=f"strategy=replan source={source}: "
                             f"{reason or ''}".rstrip(),
                      before=before, after=None)
            self.set_plan(state,
                          new_goal_label or plan.goal, list(new_steps),
                          tick, reason="replanned",
                          goal_id=goal_id if goal_id is not None
                          else old_goal_id)
            return "recovered_replan"
        return reject("recovery_rejected",
                      f"unknown recovery strategy {strategy!r}")

    # ---------------- context ----------------
    def current_step_view(self, state) -> dict | None:
        plan = state.current_plan
        if plan is None:
            return None
        step = plan.steps[plan.current_step] \
            if plan.current_step < len(plan.steps) else None
        # V0.2-B: "step" stays the step's human-readable objective
        # text (the shape Phase C tests and the audit read); the
        # structured fields ride alongside for the generic generator.
        return {"plan_id": plan.plan_id, "goal": plan.goal,
                "goal_id": plan.goal_id,
                "step_index": plan.current_step,
                "step": step.objective if step else None,
                "step_id": step.step_id if step else None,
                "action_candidates": list(step.action_candidates) if step else [],
                "success_condition": step.success_condition if step else None,
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
        # V0.2-B (design 4.5): a plan_update naming a goal_id the
        # agent does not own is invalid — the LINK is rejected and
        # recorded (the plan update itself proceeds unlinked), the
        # same ownership discipline as selected_goal_id.
        if pu.goal_id is not None \
                and pu.goal_id not in {g.key for g in state.goals}:
            self._log(state, tick, "plan_update_rejected_invalid_goal_id",
                      plan.plan_id if plan else None,
                      reason=f"goal_id {pu.goal_id!r} is not one of "
                             "this agent's goals",
                      before=self._snap(plan), after=self._snap(plan))
            pu = pu.model_copy(update={"goal_id": None})
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
            return "abandoned"

        if pu.status == "replace" and pu.new_steps:
            self.set_plan(state, pu.new_goal or (plan.goal if plan else "new_goal"),
                          pu.new_steps, tick, reason=pu.reason,
                          goal_id=pu.goal_id if pu.goal_id is not None
                          else (plan.goal_id if plan else None))
            return "replaced"

        if pu.status == "advance" and plan:
            # V0.2-B B3 — BREAKING SEMANTICS CHANGE (design 5.5):
            # provider advance no longer unblocks a blocked plan and
            # no longer asserts fulfilment of a CONDITIONAL step.
            # Blocked plans leave blocked only through the recovery
            # state machine (execute_recovery); conditional steps
            # advance only through the StepEvaluator. Provider
            # advance keeps exactly one meaning: "the provider
            # asserts this LEGACY (condition-free) step is done".
            if plan.status == "blocked":
                self._log(state, tick, "advance_rejected_blocked",
                          plan.plan_id,
                          reason="advance on a blocked plan is "
                                 "illegal in B3; use plan_update."
                                 "recovery (retry/replan/suspend/"
                                 "abandon/skip)",
                          before=self._snap(plan), after=self._snap(plan))
                return "advance_rejected_blocked"
            if plan.status != "active":
                self._log(state, tick, "advance_rejected_not_active",
                          plan.plan_id, reason=f"plan is {plan.status}",
                          before=self._snap(plan), after=self._snap(plan))
                return "advance_rejected_not_active"
            cur = plan.steps[plan.current_step] \
                if plan.current_step < len(plan.steps) else None
            if cur is not None and cur.success_condition is not None:
                self._log(state, tick,
                          "advance_rejected_conditional_step",
                          plan.plan_id,
                          reason="conditional steps advance only via "
                                 "the StepEvaluator (result-driven)",
                          before=self._snap(plan), after=self._snap(plan))
                return "advance_rejected_conditional_step"
            return self._advance(state, plan, action_sig, tick)

        self._log(state, tick, pu.status, plan.plan_id if plan else None,
                  before=self._snap(plan), after=self._snap(plan))
        return pu.status

    def _advance(self, state, plan: PlanState, action_sig: str,
                 tick: int, reason: str | None = None) -> str:
        step_idx = plan.current_step
        # V0.2-B: steps are PlanStep objects; the history log keeps
        # the step's objective TEXT (the shape tests/audit read).
        step_name = plan.steps[step_idx].objective \
            if step_idx < len(plan.steps) else None
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
        if step_idx < len(plan.steps):
            plan.steps[step_idx].status = "fulfilled"
            plan.steps[step_idx].attempts = \
                plan.steps[step_idx].attempts  # frozen with the step
        nxt = next((i for i in range(step_idx + 1, len(plan.steps))
                    if plan.steps[i].status in ("pending", "active")),
                   len(plan.steps))
        plan.current_step = nxt
        if nxt < len(plan.steps):
            plan.steps[nxt].status = "active"
        plan.last_progress_tick = tick
        plan.commitment = round(min(MAX_COMMITMENT,
                                    plan.commitment + PROGRESS_BONUS), 3)
        plan.commitment_updated_tick = tick
        if nxt >= len(plan.steps):
            plan.status = "completed"
            state.current_plan = None
            skipped = sum(1 for s in plan.steps if s.status == "skipped")
            self._log(state, tick, "advance", plan.plan_id, reason=reason,
                      before=before, after=self._snap(plan))
            # design 5.6: plan completion is its own record and must
            # carry the count of skipped steps
            self._log(state, tick, "plan_completed", plan.plan_id,
                      reason=f"all steps fulfilled or skipped "
                             f"(skipped={skipped}); final advance: "
                             f"{reason or ''}".rstrip(),
                      before=before, after=self._snap(plan))
        else:
            self._log(state, tick, "advance", plan.plan_id, reason=reason,
                      before=before, after=self._snap(plan))
        return "advanced"
