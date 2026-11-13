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

import json

from . import conditions, goal_scheduler
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

        def settle_pause(goal) -> None:
            """Close out a pause episode's bookkeeping (V0.2-C C1):
            the ticks spent paused accumulate exactly once, when
            the goal leaves the paused status by any path."""
            if goal.paused_since is not None:
                goal.cumulative_paused_ticks += tick - goal.paused_since
                goal.paused_since = None

        # V0.2-C C1: scheduler bookkeeping, consumed at the
        # plan_event evaluation point of decide() — the scheduler
        # itself writes nothing; its decision is applied HERE, by
        # the sole writer of goal state. last_pursued_tick is raw
        # material for the utility's aging term; a pursuit CHANGE
        # additionally lands as a pursuit_changed record whose
        # reason carries the full utility table (design 2.9).
        sched = getattr(agent, "_pending_schedule", None)
        if sched is not None and cause == "plan_event":
            agent._pending_schedule = None
            pursuit_key = sched.get("pursuit_goal_id")
            if pursuit_key is not None:
                for goal in st.goals:
                    if goal.key == pursuit_key:
                        before_p = goal.model_dump()
                        goal.last_pursued_tick = tick
                        if sched.get("switched"):
                            record(goal, "pursuit_changed",
                                   "scheduler pursuit: "
                                   f"{sched.get('previous_pursuit')!r} -> "
                                   f"{pursuit_key!r}; utilities="
                                   + json.dumps(sched.get("utilities", {}),
                                                sort_keys=True),
                                   before_p)
                        break

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
                # V0.2-C C1 starvation protection (design 2.3/2.5):
                # a continuous pause that reaches the goal's cap
                # force-reactivates the goal and opens a guaranteed
                # pursuit window (protection_until), inside which
                # the pause policy refuses to pause it again and the
                # scheduler locks it as pursuit.
                if goal.status == "paused":
                    cap = goal_scheduler.max_pause_for(goal)
                    if cap is not None and goal.paused_since is not None \
                            and tick - goal.paused_since >= cap:
                        settle_pause(goal)
                        goal.status = "active"
                        goal.protection_until = tick + max(
                            1, goal_scheduler.CONFIG.min_dwell)
                        record(goal, "starvation_protection",
                               f"max_pause_exceeded (cap {cap} ticks); "
                               "starvation_protection: forced "
                               "reactivation, guaranteed pursuit "
                               f"window until tick "
                               f"{goal.protection_until}", before)
                        before = goal.model_dump()
                # failure BEFORE completion (design 4.2)
                if goal.failure_condition is not None and \
                        conditions.is_satisfied(goal.failure_condition,
                                                view):
                    settle_pause(goal)
                    goal.status = "failed"
                    record(goal, "goal_failed",
                           "failure condition satisfied", before)
                    continue
                if goal.kind == "achievement" \
                        and goal.success_condition is not None:
                    if conditions.is_satisfied(goal.success_condition,
                                                view):
                        settle_pause(goal)
                        goal.status = "completed"
                        goal.progress = 1.0
                        record(goal, "goal_completed",
                               "success condition satisfied", before)
                        continue
                    # V0.2-C C1 (design 2.6): progress is MONOTONIC
                    # — it records the best satisfaction ever seen;
                    # completion still requires the CURRENT view to
                    # satisfy the condition (checked just above), and
                    # leaf-level candidacy still reads live
                    # satisfaction, so a world that genuinely
                    # regresses is not hidden — only the progress
                    # SIGNAL stops oscillating.
                    new_progress = max(
                        goal.progress or 0.0,
                        round(conditions.progress_of(
                            goal.success_condition, view), 4))
                    if goal.progress is None or \
                            new_progress - goal.progress >= 0.001:
                        goal.progress = new_progress
                        record(goal, "goal_progress",
                               "success-condition satisfaction changed",
                               before)
                        before = goal.model_dump()
                # paused rule (evaluator-side, design 4.2), now a
                # POLICY decision (V0.2-C C1, design 2.1): pursuit
                # displaced by another goal's plan pauses the goal
                # only when the scheduler's pause policy allows it
                # and the goal is not itself the scheduled pursuit.
                # A protected goal stays active; the contention is
                # resolved at the plan layer (suspend) instead.
                if goal.kind == "achievement":
                    pursuit_key = st.scheduler_memory.get("pursuit")
                    # V0.2-C C1 refinement (calibration finding): a
                    # dependency-GATED goal is not pursuit-eligible,
                    # so it has no pursuit to be displaced FROM —
                    # pausing it would only manufacture pause
                    # bookkeeping (and eventually a spurious
                    # starvation) for a goal that was never in
                    # contention. The pause rule therefore applies
                    # only to pursuit-eligible goals.
                    if goal.status == "active" and plan is not None \
                            and plan_goal_id is not None \
                            and plan_goal_id != goal.key \
                            and goal.key != pursuit_key \
                            and goal_scheduler.deps_completed(
                                goal, {g.key: g for g in st.goals}) \
                            and goal_scheduler.may_pause(goal, tick):
                        goal.status = "paused"
                        goal.paused_since = tick
                        goal.pause_count += 1
                        record(goal, "goal_paused",
                               f"plan pursuit switched to {plan_goal_id}",
                               before)
                    elif goal.status == "paused" and (
                            plan is None or plan_goal_id == goal.key
                            or (first_key == goal.key and plan is None)):
                        settle_pause(goal)
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
                # V0.2-C C1 (design 2.3): the priority floor binds
                # the recompute from below (selection magnitude
                # only — it never exempts a goal from pausing).
                new_priority = max(goal.priority_floor, min(
                    1.5, round(goal.weight + delta, 4)))
                if new_priority != goal.priority_current:
                    before_p = goal.model_dump()
                    goal.priority_current = new_priority
                    record(goal, "priority_change",
                           f"priority rules net {delta:+.2f}", before_p)
        return changes
