"""V0.2-C C1 tests: the Goal Scheduler.

Pure-function level first (utility arithmetic, dependency gating,
hysteresis / dwell / protection, the pause policy), then the
load-time depends_on contract, then evaluator-level semantics
(monotonic progress, pause bookkeeping, starvation protection),
then the plan-layer contention path. Trajectory-level gates live
in the C1 stage record (docs/V0.2-C_IMPLEMENTATION_REPORT.md).
"""
from __future__ import annotations

import pytest

from v02 import goal_scheduler
from v02.config import load_all, validate_goal_dependencies
from v02.goal_scheduler import (SchedulerConfig, dependents_map,
                                deps_completed, eligible_goals, may_pause,
                                max_pause_for, schedule, utility)
from v02.protocol import Goal

# The formula tests pin their own coefficients (they test the
# arithmetic, not the calibrated production values).
CFG = SchedulerConfig(dep=0.6, wait=0.3, switch_margin=0.10)


def goal(key, weight=1.0, status="active", **kw):
    return Goal(key=key, desc=key, weight=weight,
                priority_current=kw.pop("priority_current", weight),
                status=status, **kw)


# ---------------- utility arithmetic ----------------

def test_utility_dependency_traction_matches_the_clinic_arithmetic():
    brief = goal("brief_nurse", 0.85)
    ensure = goal("ensure_readiness", 1.0, depends_on=["brief_nurse"])
    goals = [brief, ensure]
    by_key = {g.key: g for g in goals}
    deps = dependents_map(goals)
    # brief is pulled up by its unfinished dependent: .85 + .6*1.0
    u_brief = utility(brief, goals, by_key, deps, tick=5,
                      incumbent_key=None, plan_commitment=None, cfg=CFG)
    assert u_brief == pytest.approx(0.85 + 0.6 * 1.0 + 0.3 * 1.0)
    # ensure gets no traction (nothing depends on it)
    u_ensure = utility(ensure, goals, by_key, deps, tick=5,
                       incumbent_key=None, plan_commitment=None, cfg=CFG)
    assert u_ensure == pytest.approx(1.0 + 0.3 * 1.0)


def test_utility_aging_saturates_and_switch_cost_applies():
    g = goal("g", 1.0, last_pursued_tick=0)
    goals = [g]
    by_key = {"g": g}
    deps = dependents_map(goals)
    # 5 ticks unpursued of a 10-tick scale: half the aging term
    u = utility(g, goals, by_key, deps, tick=5, incumbent_key="other",
                plan_commitment=0.5, cfg=CFG)
    assert u == pytest.approx(1.0 + 0.3 * 0.5)
    # a committed incumbent plan (>= 0.7) prices the switch
    u2 = utility(g, goals, by_key, deps, tick=5, incumbent_key="other",
                 plan_commitment=0.8, cfg=CFG)
    assert u2 == pytest.approx(1.0 + 0.3 * 0.5 - 0.2)
    # the incumbent itself never pays the switch cost
    u3 = utility(g, goals, by_key, deps, tick=5, incumbent_key="g",
                 plan_commitment=0.8, cfg=CFG)
    assert u3 == pytest.approx(1.0 + 0.3 * 0.5)


def test_dependents_map_and_gating():
    a = goal("a")
    b = goal("b", depends_on=["a"])
    goals = [a, b]
    by_key = {g.key: g for g in goals}
    assert dependents_map(goals)["a"] == ["b"]
    assert deps_completed(b, by_key) is False
    assert [g.key for g in eligible_goals(goals, by_key)] == ["a"]
    a.status = "completed"
    assert deps_completed(b, by_key) is True
    assert [g.key for g in eligible_goals(goals, by_key)] == ["b"]


def test_paused_goals_are_pursuit_eligible_but_inactive_are_not():
    a = goal("a", status="paused")
    b = goal("b", status="inactive")
    c = goal("c", status="completed")
    goals = [a, b, c]
    by_key = {g.key: g for g in goals}
    assert [g.key for g in eligible_goals(goals, by_key)] == ["a"]


# ---------------- schedule decision: hysteresis / dwell / protection --

def test_schedule_picks_highest_utility_and_breaks_ties_by_key():
    a = goal("aaa", 1.0)
    b = goal("bbb", 1.0)
    out = schedule([b, a], None, tick=3, memory={"pursuit": None,
                                                 "since": 0}, cfg=CFG)
    assert out["pursuit_goal_id"] == "aaa"
    assert out["switched"] is True
    assert out["memory"] == {"pursuit": "aaa", "since": 3}


def test_hysteresis_and_dwell_hold_the_incumbent():
    inc = goal("inc", 1.0, last_pursued_tick=4)
    chal = goal("chal", 1.05, last_pursued_tick=4)
    goals = [inc, chal]
    # tick 5: dwell (since 4) not met -> incumbent holds despite the
    # challenger edging the raw table
    out = schedule(goals, None, tick=5, memory={"pursuit": "inc",
                                                "since": 4}, cfg=CFG)
    assert out["pursuit_goal_id"] == "inc"
    # tick 7: dwell met, but the margin (0.10) is not beaten
    out = schedule(goals, None, tick=7, memory={"pursuit": "inc",
                                                "since": 4}, cfg=CFG)
    assert out["pursuit_goal_id"] == "inc"
    # a clearly stronger challenger switches once dwell is met
    chal2 = goal("chal", 1.3, last_pursued_tick=4)
    out = schedule([inc, chal2], None, tick=7,
                   memory={"pursuit": "inc", "since": 4}, cfg=CFG)
    assert out["pursuit_goal_id"] == "chal"
    assert out["switched"] is True


def test_margin_waived_when_incumbent_loses_eligibility():
    inc = goal("inc", 1.0, status="completed")
    chal = goal("chal", 0.5)
    out = schedule([inc, chal], None, tick=5,
                   memory={"pursuit": "inc", "since": 4}, cfg=CFG)
    assert out["pursuit_goal_id"] == "chal"


def test_fresh_activation_preempts_dwell():
    inc = goal("inc", 1.0, last_pursued_tick=4)
    chal = goal("chal", 1.3, last_pursued_tick=4)
    out = schedule([inc, chal], None, tick=5,
                   memory={"pursuit": "inc", "since": 4}, cfg=CFG,
                   newly_activated={"chal"})
    assert out["pursuit_goal_id"] == "chal"


def test_protection_window_locks_pursuit():
    weak = goal("weak", 0.4, protection_until=9)
    strong = goal("strong", 1.4)
    out = schedule([weak, strong], None, tick=7,
                   memory={"pursuit": "strong", "since": 0}, cfg=CFG)
    assert out["pursuit_goal_id"] == "weak"
    # after the window lapses the table rules again
    out = schedule([weak, strong], None, tick=10,
                   memory={"pursuit": "strong", "since": 0}, cfg=CFG)
    assert out["pursuit_goal_id"] == "strong"


def test_dependency_gated_goal_is_never_pursuit():
    brief = goal("brief_nurse", 0.85)
    ensure = goal("ensure_readiness", 1.5, depends_on=["brief_nurse"])
    out = schedule([brief, ensure], None, tick=3,
                   memory={"pursuit": None, "since": 0}, cfg=CFG)
    assert out["pursuit_goal_id"] == "brief_nurse"
    assert out["eligible_goal_ids"] == ["brief_nurse"]


# ---------------- pause policy ----------------

def test_may_pause_policy():
    assert may_pause(goal("g"), 3, CFG) is True
    assert may_pause(goal("g", pausable=False), 3, CFG) is False
    assert may_pause(goal("g", kind="maintenance"), 3, CFG) is False
    assert may_pause(goal("g", protection_until=5), 5, CFG) is False
    assert may_pause(goal("g", protection_until=5), 6, CFG) is True


def test_max_pause_for_resolution():
    assert max_pause_for(goal("g"), CFG) == 8
    assert max_pause_for(goal("g", max_pause_ticks=3), CFG) == 3
    assert max_pause_for(goal("g", starvation_protection=False),
                         CFG) is None


# ---------------- depends_on load contract ----------------

def test_depends_on_unknown_key_fails_at_load():
    with pytest.raises(ValueError, match="same-character only"):
        validate_goal_dependencies(
            [{"key": "a", "depends_on": ["someone_elses_goal"]}],
            "character x")


def test_depends_on_cycle_fails_at_load():
    with pytest.raises(ValueError, match="cycle"):
        validate_goal_dependencies(
            [{"key": "a", "depends_on": ["b"]},
             {"key": "b", "depends_on": ["a"]}], "character x")


def test_depends_on_self_reference_fails_at_load():
    with pytest.raises(ValueError, match="names itself"):
        validate_goal_dependencies(
            [{"key": "a", "depends_on": ["a"]}], "character x")


def test_clinic_doctor_dependency_loads():
    cfg = load_all(root=_clinic_root())["doctor_shen"]
    goals = {g.key: g for g in cfg.initial_state().goals}
    assert goals["ensure_readiness"].depends_on == ["brief_nurse"]


def _clinic_root():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "characters_scenario_c"


# ---------------- evaluator semantics ----------------

def _perc(tick=1, loc="bar", events=()):
    return {"tick": tick, "time": "21:05", "location": loc,
            "colocated": [], "events": list(events), "muffled": [],
            "visible_objects": [], "agent_id": "x",
            "world_public": {"weather": "clear"}}


def _make_agent(cid="owner"):
    from v02.agent import CharacterAgent
    from v02.providers.rule_provider import RuleProvider
    cfg = load_all()[cid]
    return CharacterAgent(cfg, RuleProvider(cfg, seed=42))


def test_progress_is_monotonic_under_a_regressing_world():
    agent = _make_agent("soldier")
    agent.evaluate_goals(1, cause="perception")
    agent.memory.update_belief("anonymous_letter_exists", 1.0,
                               "experienced", "亲眼读到",
                               source_type="direct_observation",
                               event_id="E1", tick=2)
    agent.evaluate_goals(2, cause="perception")
    g = {x.key: x for x in agent.state.goals}["complete_investigation"]
    assert g.progress == 0.5
    # the world regresses (the belief is removed from the fixture):
    # current satisfaction falls, recorded progress must NOT
    agent.state.beliefs = [b for b in agent.state.beliefs
                           if b.key != "anonymous_letter_exists"]
    agent.evaluate_goals(3, cause="perception")
    g = {x.key: x for x in agent.state.goals}["complete_investigation"]
    assert g.progress == 0.5
    assert g.status == "active"      # completion still reads the
    #                                  current view, never the peak


def test_priority_floor_binds_the_recompute():
    agent = _make_agent("owner")
    g = {x.key: x for x in agent.state.goals}["avoid_politics"]
    g.priority_floor = 0.95
    g.priority_rules = [{
        "when": {"type": "belief_threshold",
                 "belief": "anonymous_letter_exists",
                 "min_confidence": 0.5},
        "delta": -0.3, "once": False}]
    agent.evaluate_goals(1, cause="perception")
    # weight 0.9 - 0.3 = 0.6, floored at 0.95
    assert g.priority_current == 0.95


def test_pause_bookkeeping_and_starvation_reactivation():
    agent = _make_agent("owner")
    by_key = {g.key: g for g in agent.state.goals}
    chase = by_key["find_letter_source"]
    chase.status = "active"          # fixture: pretend it activated
    pm = agent.plan_manager
    from v02.protocol import PlanStep
    pm.set_plan(agent.state, "politics", [PlanStep(step_id="s1")], 2,
                goal_id="avoid_politics")
    agent.evaluate_goals(2, cause="plan_event")
    assert chase.status == "paused"
    assert chase.paused_since == 2 and chase.pause_count == 1
    # it stays paused while the other plan serves; at cap (8 ticks
    # from tick 2) the starvation protection force-reactivates it
    for tick in range(3, 10):
        agent.evaluate_goals(tick, cause="perception")
    assert chase.status == "paused"
    agent.evaluate_goals(10, cause="perception")
    assert chase.status == "active"
    assert chase.cumulative_paused_ticks == 8
    assert chase.paused_since is None
    assert chase.protection_until is not None \
        and chase.protection_until >= 10
    kinds = [c["kind"] for c in agent._drain_goal_changes()]
    assert "starvation_protection" in kinds


def test_unpausable_goal_is_not_paused_by_a_plan_switch():
    agent = _make_agent("owner")
    by_key = {g.key: g for g in agent.state.goals}
    chase = by_key["find_letter_source"]
    chase.status = "active"
    chase.pausable = False
    pm = agent.plan_manager
    from v02.protocol import PlanStep
    pm.set_plan(agent.state, "politics", [PlanStep(step_id="s1")], 2,
                goal_id="avoid_politics")
    agent.evaluate_goals(2, cause="plan_event")
    assert chase.status == "active"


def test_contention_suspends_the_incumbent_plan():
    """Design 2.1: the pursuit wants the slot, the incumbent goal
    is pause-protected -> the PLAN suspends (state preserved), the
    goal does not pause, and the evaluator reactivates the plan
    when its step condition comes to hold."""
    import asyncio
    from v02.protocol import AgentDecision, ActionSpec, PlanUpdate
    agent = _make_agent("soldier")
    by_key = {g.key: g for g in agent.state.goals}
    inc = by_key["complete_investigation"]
    inc.pausable = False
    pm = agent.plan_manager
    from v02.protocol import PlanStep
    step = PlanStep(
        step_id="gather", objective="gather",
        action_candidates=[{"type": "ask"}],
        success_condition={"type": "belief_threshold",
                           "belief": "anonymous_letter_exists",
                           "min_confidence": 0.5})
    pm.set_plan(agent.state, "inv", [step], 1,
                goal_id="complete_investigation")
    decision = AgentDecision(
        intent="x", action=ActionSpec(type="wait"),
        plan_update=PlanUpdate(
            status="replace", reason="scheduler switch",
            new_goal="mayor", new_steps=[PlanStep(step_id="n1")],
            goal_id="learn_mayor_truth"))
    agent._tick = 2
    note = agent._apply_plan_update(decision)
    assert note == "contention_suspended"
    plan = agent.state.current_plan
    assert plan is not None and plan.status == "suspended"
    assert plan.goal_id == "complete_investigation"
    assert plan.steps[0].step_id == "gather"     # state preserved
    assert inc.status == "active"                # never paused
    # the suspended plan reactivates through the evaluator when its
    # reactivate_when (the step's success condition) comes to hold
    agent.memory.update_belief("anonymous_letter_exists", 1.0,
                               "experienced", "亲眼读到",
                               source_type="direct_observation",
                               event_id="E7", tick=3)
    agent.evaluate_goals(3, cause="perception")
    assert agent.state.current_plan.status == "active"
    kinds = [t["kind"] for t in pm.transitions]
    assert "recovery" in kinds


def test_pursuit_changed_record_and_last_pursued_writeback():
    agent = _make_agent("owner")
    agent._pending_schedule = {
        "pursuit_goal_id": "find_letter_source",
        "previous_pursuit": None, "switched": True,
        "utilities": {"find_letter_source": 1.2}}
    by_key = {g.key: g for g in agent.state.goals}
    by_key["find_letter_source"].status = "active"
    agent.evaluate_goals(4, cause="plan_event")
    assert by_key["find_letter_source"].last_pursued_tick == 4
    kinds = [c["kind"] for c in agent._drain_goal_changes()]
    assert "pursuit_changed" in kinds
    # consumed: a second plan_event evaluation does not re-emit
    agent._drain_goal_changes()
    agent.evaluate_goals(5, cause="plan_event")
    kinds = [c["kind"] for c in agent._drain_goal_changes()]
    assert "pursuit_changed" not in kinds


def test_set_config_actually_reaches_schedule_defaults():
    """Regression for the calibration-v1 bug: cfg defaults must
    resolve at CALL time — a default bound at import made every
    grid combo silently run the same coefficients (all 18 screen
    rows came out identical)."""
    a = goal("aaa", 1.0)
    b = goal("bbb", 0.9, depends_on=[])
    dep_goal = goal("ccc", 0.5, depends_on=["bbb"])
    goals = [a, b, dep_goal]
    # with dep=0: pursuit is the raw-priority leader aaa
    goal_scheduler.set_config(SchedulerConfig(dep=0.0, wait=0.0))
    try:
        out = schedule(goals, None, tick=3,
                       memory={"pursuit": None, "since": 0})
        assert out["pursuit_goal_id"] == "aaa"
        # with dep=2.0: bbb is pulled above aaa by its dependent
        goal_scheduler.set_config(SchedulerConfig(dep=2.0, wait=0.0))
        out = schedule(goals, None, tick=3,
                       memory={"pursuit": None, "since": 0})
        assert out["pursuit_goal_id"] == "bbb"
    finally:
        goal_scheduler.set_config(SchedulerConfig())


def test_dependency_gated_goal_is_not_paused():
    """Calibration finding (clinic seed 7): ensure_readiness sat
    paused while dependency-gated behind brief_nurse and tripped a
    spurious starvation at the cap. A gated goal is never in
    contention, so the pause rule must not touch it."""
    agent = _make_agent("owner")
    by_key = {g.key: g for g in agent.state.goals}
    gated = by_key["find_letter_source"]
    gated.status = "active"
    gated.depends_on = ["protect_tavern"]   # achievement dep that
    #                                          never completes here
    protector = by_key["protect_tavern"]
    protector.kind = "achievement"
    pm = agent.plan_manager
    from v02.protocol import PlanStep
    pm.set_plan(agent.state, "politics", [PlanStep(step_id="s1")], 2,
                goal_id="avoid_politics")
    for tick in range(2, 14):
        agent.evaluate_goals(tick, cause="plan_event")
    assert gated.status == "active"
    assert gated.pause_count == 0
    kinds = [c["kind"] for c in agent._drain_goal_changes()
             if c["goal_key"] == gated.key]
    assert "goal_paused" not in kinds
    assert "starvation_protection" not in kinds


def test_scheduler_config_snapshot_shape():
    import dataclasses
    snap = dataclasses.asdict(goal_scheduler.CONFIG)
    assert set(snap) == {"dep", "wait", "wait_scale", "switch",
                         "switch_margin", "min_dwell",
                         "max_pause_ticks"}
