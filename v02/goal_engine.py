"""Goal Engine (V0.2-B B2, design section 4).

The GoalEvaluator is the ONLY writer of goal state. It evaluates
each goal's declared conditions — activation / success / failure /
priority rules — against the agent's SUBJECTIVE view (v02/conditions)
at pinned times: run init, the end of receive() (perception), and
after a plan update inside decide() (plan_event).

Semantics pinned by the design:

- Four layers stay separate: an action's success, a plan step's
  fulfilment, a plan's completion and a GOAL's completion are
  independent judgements. In particular NOTHING here reads plan
  progress: the old _sync_goal_* (progress = current_step/len(steps),
  plan completion -> goal completion) is deleted, and a completed
  plan whose goal condition is unmet leaves the goal active with
  its condition-derived progress. (The review's key example — the
  keeper searches, finds nothing, and the goal is NOT complete —
  is a named regression test.)
- progress = the success_condition's leaf satisfaction (conditions.
  progress_of); maintenance goals carry no progress (None).
- failure is checked BEFORE completion.
- priority_current is recomputed statelessly from the static weight
  plus currently-effective rule deltas (once-rules latch); the
  weight itself never changes.
- the paused rule is the evaluator's, not the plan layer's: an
  achievement goal whose pursuit was displaced by another goal's
  plan pauses; regaining a linked plan (or becoming priority-first
  with no current plan) reactivates it.
- goal changes are recorded in the existing goal_changes shape and
  drained by the agent into the audit chain.

The evaluator also hosts the suspended-plan reactivation check
(design 5.5): a suspended plan whose reactivate_when condition
becomes satisfied returns to active here, because this is the
component that already re-evaluates conditions every tick.
"""
from __future__ import annotations

from . import conditions
from .conditions import build_view


class GoalEvaluator:
    def __init__(self):
        self._latched: set[tuple[str, int]] = set()   # (goal_key, rule idx)

    # ---------------- public ----------------
    def evaluate(self, agent, tick: int, cause: str) -> list[dict]:
        changes: list[dict] = []
        st = agent.state

        def record(goal, kind: str, reason: str, before: dict) -> None:
            changes.append(
                {"tick": tick, "character_id": st.character_id,
                 "goal_key": goal.key, "kind": kind,
                 "reason": f"{reason} (cause={cause})",
                 "before": before, "after": goal.model_dump()})

        # suspended-plan reactivation (design 5.5): checked on the
        # same cadence as goal conditions
        plan = st.current_plan
        if plan is not None and plan.status == "suspended" \
                and plan.reactivate_when:
            view = build_view(agent, tick)
            if conditions.is_satisfied(plan.reactivate_when, view):
                agent.plan_manager.execute_recovery(
                    agent.state, tick, "suspend",
                    reason="reactivate_condition_met", source="evaluator")

        plan_goal_id = plan.goal_id if plan is not None else None
        # priority-first among pursuit-eligible goals (for the
        # paused rule's reactivation arm)
        eligible = [g for g in st.goals if g.status in ("active", "paused")]
        first_key = None
        if eligible:
            first_key = sorted(
                eligible, key=lambda g: (-g.priority_current, g.key))[0].key

        for goal in st.goals:
            view = build_view(agent, tick)
            before = goal.model_dump()

            # activation: inactive -> active when its condition holds
            if goal.status == "inactive":
                if goal.activation_condition is None or \
                        conditions.is_satisfied(goal.activation_condition,
                                                view):
                    goal.status = "active"
                    record(goal, "activated",
                           "activation condition satisfied", before)
                    before = goal.model_dump()
                else:
                    continue

            if goal.status in ("active", "paused"):
                # failure BEFORE completion (design 4.2)
                if goal.failure_condition is not None and \
                        conditions.is_satisfied(goal.failure_condition,
                                                view):
                    goal.status = "failed"
                    record(goal, "goal_failed",
                           "failure condition satisfied", before)
                    continue
                if goal.kind == "achievement" \
                        and goal.success_condition is not None:
                    if conditions.is_satisfied(goal.success_condition,
                                                view):
                        goal.status = "completed"
                        goal.progress = 1.0
                        record(goal, "goal_completed",
                               "success condition satisfied", before)
                        continue
                    new_progress = round(
                        conditions.progress_of(goal.success_condition,
                                               view), 4)
                    if goal.progress is None or \
                            abs(new_progress - goal.progress) >= 0.001:
                        goal.progress = new_progress
                        record(goal, "goal_progress",
                               "success-condition satisfaction changed",
                               before)
                        before = goal.model_dump()
                # paused rule (evaluator-side, design 4.2): pursuit
                # displaced by another goal's plan
                if goal.kind == "achievement":
                    if goal.status == "active" and plan is not None \
                            and plan_goal_id is not None \
                            and plan_goal_id != goal.key:
                        goal.status = "paused"
                        record(goal, "goal_paused",
                               f"plan pursuit switched to {plan_goal_id}",
                               before)
                    elif goal.status == "paused" and (
                            plan is None or plan_goal_id == goal.key
                            or (first_key == goal.key and plan is None)):
                        goal.status = "active"
                        record(goal, "goal_reactivated",
                               "pursuit resumed", before)

            # priority: stateless recompute (weight + live deltas,
            # once-rules latched) — never drifts, never edited by
            # providers
            if goal.status not in ("completed", "failed", "abandoned"):
                delta = 0.0
                for i, rule in enumerate(goal.priority_rules or []):
                    when = rule.get("when")
                    if when is None:
                        continue
                    key = (goal.key, i)
                    if rule.get("once") and key in self._latched:
                        delta += float(rule.get("delta", 0.0))
                    elif conditions.is_satisfied(when, view):
                        delta += float(rule.get("delta", 0.0))
                        if rule.get("once"):
                            self._latched.add(key)
                new_priority = max(0.0, min(
                    1.5, round(goal.weight + delta, 4)))
                if new_priority != goal.priority_current:
                    before_p = goal.model_dump()
                    goal.priority_current = new_priority
                    record(goal, "priority_change",
                           f"priority rules net {delta:+.2f}", before_p)
        return changes
