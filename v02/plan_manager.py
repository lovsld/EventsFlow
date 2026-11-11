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

DECAY_PER_TICK = 0.02
MIN_COMMITMENT = 0.1
MAX_COMMITMENT = 0.95
PROGRESS_BONUS = 0.05


class PlanManager:
    def __init__(self):
        self.transitions: list[dict] = []

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
        return plan

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
            return "abandoned"

        if pu.status == "replace" and pu.new_steps:
            self.set_plan(state, pu.new_goal or (plan.goal if plan else "new_goal"),
                          pu.new_steps, tick, reason=pu.reason)
            return "replaced"

        if pu.status == "advance" and plan and plan.status == "active":
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
        plan.last_progress_tick = tick
        plan.commitment = round(min(MAX_COMMITMENT,
                                    plan.commitment + PROGRESS_BONUS), 3)
        plan.commitment_updated_tick = tick
        if plan.current_step >= len(plan.steps):
            plan.status = "completed"
            state.current_plan = None
            self._log(state, tick, "advance", plan.plan_id, reason=reason,
                      before=before, after=self._snap(plan))
        else:
            self._log(state, tick, "advance", plan.plan_id, reason=reason,
                      before=before, after=self._snap(plan))
        return "advanced"
