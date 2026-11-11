"""V0.2-A Phase 3 tests: evidence-based beliefs, belief-change
history completion, the structured Goal lifecycle, plan blocked
state and the commitment replacement gate.

The belief tests implement the task book's section-13 group C
semantics; the goal tests cover group D's multi-goal behaviour.
"""
from __future__ import annotations

import pytest

from v02.agent import CharacterAgent
from v02.config import load_all
from v02.memory import AgentMemory
from v02.protocol import Belief, CharacterState, PlanUpdate


def st(name):                    # steps are plain names to set_plan
    return name


def fresh_memory(cid="soldier"):
    cfg = load_all()[cid]
    return AgentMemory(cfg.initial_state())


def perc(tick=1, loc="tables", events=()):
    return {"tick": tick, "time": "21:05", "location": loc,
            "colocated": [], "events": list(events), "muffled": [],
            "visible_objects": [], "agent_id": "x",
            "world_public": {"weather": "clear"}}


# ---------- C: belief evidence semantics ----------

def test_hearsay_then_direct_upgrades_kind_and_keeps_both_evidence():
    mem = fresh_memory()
    mem.update_belief("anonymous_letter_exists", 0.7, "heard", "听行商说的",
                      event_id="E100", tick=1, source="merchant")
    b = mem.belief("anonymous_letter_exists")
    assert b.confidence == 0.7 and b.kind == "heard"
    assert len(b.evidence) == 1 and b.evidence[0].source_type == "testimony"
    # direct evidence arrives: confidence and source grade both rise
    mem.update_belief("anonymous_letter_exists", 1.0, "experienced",
                      "亲眼打开抽屉读到的",
                      source_type="direct_observation",
                      event_id="E200", tick=2)
    b = mem.belief("anonymous_letter_exists")
    assert b.confidence == 1.0 and b.kind == "experienced"
    assert len(b.evidence) == 2                       # history not lost
    assert {e.source_type for e in b.evidence} == \
        {"testimony", "direct_observation"}


def test_direct_evidence_is_not_dragged_down_by_later_hearsay():
    mem = fresh_memory()
    mem.update_belief("letter", 1.0, "experienced", "亲见",
                      source_type="direct_observation",
                      event_id="E1", tick=1)
    mem.update_belief("letter", 0.7, "heard", "又听人说",
                      event_id="E2", tick=2, source="merchant")
    b = mem.belief("letter")
    assert b.confidence == 1.0 and b.kind == "experienced"
    assert len(b.evidence) == 2


def test_same_event_never_counts_twice():
    mem = fresh_memory()
    mem.update_belief("letter", 0.5, "heard", "听说", event_id="E1",
                      tick=1, source="merchant")
    mem.update_belief("letter", 0.9, "heard", "同一事件的不同转述",
                      event_id="E1", tick=1, source="merchant")
    b = mem.belief("letter")
    assert len(b.evidence) == 1 and b.confidence == 0.5


def test_correlated_testimony_does_not_stack_but_independent_does():
    mem = fresh_memory()
    # the same speaker repeating themselves is one channel
    mem.update_belief("x", 0.5, "heard", "", event_id="E1", tick=1,
                      source="merchant")
    mem.update_belief("x", 0.5, "heard", "", event_id="E2", tick=2,
                      source="merchant")
    assert mem.belief("x").confidence == 0.5
    # a second, independent speaker corroborates: 1-(1-.5)(1-.5)
    mem.update_belief("x", 0.5, "heard", "", event_id="E3", tick=3,
                      source="owner")
    assert mem.belief("x").confidence == 0.75


def test_confidence_stays_within_bounds():
    mem = fresh_memory()
    for i in range(6):
        mem.update_belief("y", 0.5, "heard", "", event_id=f"E{i}",
                          tick=i, source=f"speaker{i}")
    conf = mem.belief("y").confidence
    assert 0.5 < conf < 1.0          # corroboration, never certainty


def test_legacy_belief_migrates_to_one_equivalent_evidence():
    b = Belief(key="x", confidence=0.7, kind="heard", desc="旧格式")
    assert len(b.evidence) == 1
    assert b.evidence[0].source_type == "testimony"
    assert b.evidence[0].reliability == 0.7
    raw = load_all()["soldier"].initial_state().model_dump()
    raw["beliefs"] = [{"key": "old", "confidence": 0.6,
                       "kind": "inferred", "desc": ""}]
    state = CharacterState.model_validate(raw)
    migrated = state.beliefs[0]
    assert len(migrated.evidence) == 1
    assert migrated.evidence[0].source_type == "inference"


# ---------- 3-2: interpretation path lands in belief history ----------

def test_interpretation_belief_change_is_recorded():
    cfg = load_all()["merchant"]
    from v02.providers.rule_provider import RuleProvider
    agent = CharacterAgent(cfg, RuleProvider(cfg, seed=42))
    agent.state.location = "tables"
    ev = {"event_id": "E500", "tick": 3, "type": "speak",
          "actors": ["soldier"], "location": "tables",
          "claim": "letter_discussion", "topic": "letter_discussion",
          "text": "士兵：老板，我在后屋看见一封信。"}
    agent.receive(perc(3, "tables", events=[ev]), None)
    ups = agent.drain_chain_updates()
    rows = [u for u in ups if u["kind"] == "belief_change"
            and u["key"] == "anonymous_letter_exists"]
    assert len(rows) == 1
    assert rows[0]["cause"] == "interpretation"
    # V0.2-B adjudication (parked item P-3): the pinned value is the
    # V0.2-B design section 3.5 testimony formula — reliability =
    # base(1.0 for a declared proposition) x (0.4 + 0.6 x trust),
    # with the merchant's default trust in the soldier at 0.3:
    # 1.0 x (0.4 + 0.6 x 0.3) = 0.58. The old 0.7 pin predates the
    # formula and is arithmetically unreachable under it (the
    # formula's ceiling at trust 0.3 is exactly 0.58).
    assert rows[0]["before"] is None and rows[0]["after"] == 0.58
    # the belief itself is testimony-grade only — no leak of the
    # soldier's direct experience
    b = agent.memory.belief("anonymous_letter_exists")
    assert b.kind == "heard" and b.confidence == 0.58


# ---------- D: structured goals ----------

def test_goals_start_structured():
    cfg = load_all()["owner"]
    goals = {g.key: g for g in cfg.initial_state().goals}
    assert goals["protect_tavern"].status == "active"
    assert goals["protect_tavern"].commitment == 1.0   # defaults to weight
    assert goals["protect_tavern"].progress == 0.0
    assert goals["protect_tavern"].created_at == 0
    assert goals["protect_tavern"].deadline is None


def test_goal_progress_and_completion_follow_the_plan():
    """V0.2-B B2 rewrite (pre-registered disposition #3): the V0.2-A
    version asserted _sync semantics — plan advance writes goal
    progress (step fraction) and plan completion completes the
    goal. That coupling is exactly the review's P0 and is deleted:
    this test now asserts the SEPARATION in the review's own form —
    a plan can complete while its goal's success condition is unmet,
    and the goal then stays active with condition-derived progress;
    the goal completes only when the CONDITION holds."""
    cfg = load_all()["owner"]
    from v02.providers.rule_provider import RuleProvider
    agent = CharacterAgent(cfg, RuleProvider(cfg, seed=42))
    pm = agent.plan_manager
    # find_letter_source starts inactive (it has an activation
    # condition); activate it through the evaluator by feeding the
    # activating perception — a letter_question asked of the owner
    agent.receive(perc(1, "tables", events=[
        {"event_id": "E1", "tick": 1, "type": "ask",
         "actors": ["soldier", "owner"], "location": "tables",
         "claim": "letter_question", "topic": "letter_question",
         "text": "…"}]), None)
    goal = {g.key: g for g in agent.state.goals}["find_letter_source"]
    assert goal.status == "active"
    # (the activation change record travelled the receive chain)
    chain_kinds = [u.get("change_kind") for u in agent.drain_chain_updates()
                   if u.get("kind") == "goal_change"]
    assert "activated" in chain_kinds
    # a linked plan runs to completion — the goal does NOT follow it
    # (B3 mechanics: these are legacy steps, advanced by explicit
    # provider advances — the only path legacy steps have)
    pm.set_plan(agent.state, "find_letter_source",
                [st("find_letter_source"), st("stay_calm")], 2,
                goal_id="find_letter_source")
    pm.apply_update(agent.state, PlanUpdate(status="advance"), "a1", 3)
    pm.apply_update(agent.state, PlanUpdate(status="advance"), "a2", 4)
    assert agent.state.current_plan is None      # the plan completed
    agent.evaluate_goals(5, cause="plan_event")
    goal = {g.key: g for g in agent.state.goals}["find_letter_source"]
    assert goal.status == "active"               # the goal did not
    assert goal.progress is not None and goal.progress < 1.0
    # the goal completes when its success CONDITION holds: the owner
    # gains a strong belief about the letter's source
    agent.memory.update_belief("letter_source_identified", 0.85,
                               "inferred", "推断出了信的来源",
                               event_id="E9", tick=6)
    agent.evaluate_goals(6, cause="perception")
    goal = {g.key: g for g in agent.state.goals}["find_letter_source"]
    assert goal.status == "completed" and goal.progress == 1.0
    kinds = [c["kind"] for c in agent._drain_goal_changes()]
    assert "goal_completed" in kinds


def test_goal_pauses_on_switch_and_reactivates():
    """V0.2-B B2 rewrite (disposition #3): pausing is the
    GoalEvaluator's rule over plan LINKAGE (goal_id), not a plan-set
    side effect. A plan whose label merely equals a goal key no
    longer touches the goal at all."""
    cfg = load_all()["owner"]
    from v02.providers.rule_provider import RuleProvider
    agent = CharacterAgent(cfg, RuleProvider(cfg, seed=42))
    pm = agent.plan_manager
    # activate find_letter_source through its condition first
    agent.receive(perc(1, "tables", events=[
        {"event_id": "E1", "tick": 1, "type": "ask",
         "actors": ["soldier", "owner"], "location": "tables",
         "claim": "letter_question", "topic": "letter_question",
         "text": "…"}]), None)
    agent._drain_goal_changes()
    by_key = {g.key: g for g in agent.state.goals}
    assert by_key["find_letter_source"].status == "active"
    # a plan linked to it, then pursuit switches to another goal's
    # plan -> the evaluator pauses it at the next evaluation
    pm.set_plan(agent.state, "chase", [st("find_letter_source")], 2,
                goal_id="find_letter_source")
    agent.evaluate_goals(2, cause="plan_event")
    assert by_key["find_letter_source"].status == "active"
    pm.set_plan(agent.state, "politics", [st("deny_letter")], 3,
                goal_id="avoid_politics")
    agent.evaluate_goals(3, cause="plan_event")
    assert by_key["find_letter_source"].status == "paused"
    # pursuit returns -> reactivated
    pm.set_plan(agent.state, "chase", [st("find_letter_source")], 4,
                goal_id="find_letter_source")
    agent.evaluate_goals(4, cause="plan_event")
    assert by_key["find_letter_source"].status == "active"
    kinds = [c["kind"] for c in agent._drain_goal_changes()]
    assert "goal_paused" in kinds and "goal_reactivated" in kinds


def test_goal_abandoned_by_provider_update():
    """V0.2-B B2 rewrite (disposition #3): abandoning a PLAN no
    longer abandons its goal — the four layers are separate. The
    goal's fate is decided by its own conditions alone (here the
    maintenance goal's failure condition is unmet, so it simply
    stays active)."""
    cfg = load_all()["owner"]
    from v02.providers.rule_provider import RuleProvider
    agent = CharacterAgent(cfg, RuleProvider(cfg, seed=42))
    pm = agent.plan_manager
    pm.set_plan(agent.state, "protect_tavern", [st("protect_tavern")], 1,
                goal_id="protect_tavern")
    pm.apply_update(agent.state,
                    PlanUpdate(status="abandon", reason="不再相关"),
                    "sig", 2)
    assert agent.state.current_plan is None      # the plan is gone
    agent.evaluate_goals(3, cause="plan_event")
    by_key = {g.key: g for g in agent.state.goals}
    assert by_key["protect_tavern"].status == "active"   # goal stands
    assert "goal_abandoned" not in \
        [c["kind"] for c in agent._drain_goal_changes()]


def test_context_presents_goals_priority_first():
    """V0.2-B B2: the owner's find_letter_source has an activation
    condition (design 4.6 migration) that is unmet on a fresh state,
    so it starts INACTIVE: it is not presented among active_goals,
    and surfaces as an inactive-goal note in constraints instead.
    (Flagged in V0.2-B_REPORT.md as a disposition-#3 extension.)"""
    from v02.context_builder import build_context
    cfg = load_all()["owner"]
    from v02.providers.rule_provider import RuleProvider
    agent = CharacterAgent(cfg, RuleProvider(cfg, seed=42))
    ctx = build_context(agent, perc())
    keys = [g.key for g in ctx.active_goals]
    assert keys == ["protect_tavern", "avoid_politics"]
    assert any("find_letter_source" in c for c in ctx.constraints)


# ---------- plan blocked state & commitment gate ----------

def _owner_agent():
    cfg = load_all()["owner"]
    from v02.providers.rule_provider import RuleProvider
    agent = CharacterAgent(cfg, RuleProvider(cfg, seed=42))
    agent._tick = 1
    return agent


def test_plan_blocks_after_repeated_step_failures_and_recovers():
    """V0.2-B B3 rewrite (pre-registered disposition #4): the V0.2-A
    version used note_step_failure + a provider advance that
    unblocked. B3 semantics: per-step attempts counted by the
    StepEvaluator, blocked entered at the retry policy, provider
    advance on a blocked plan REJECTED, exit only via the recovery
    state machine; a conditional step then advances only when its
    success condition actually holds."""
    from v02.protocol import PlanStep
    cfg = load_all()["soldier"]
    from v02.providers.rule_provider import RuleProvider
    agent = CharacterAgent(cfg, RuleProvider(cfg, seed=42))
    ask = {"type": "ask", "target": "owner", "target_location": None,
           "object": None, "content": "镇上的钱呢？", "claim": None,
           "topic": "funds"}
    step = PlanStep(
        step_id="press", objective="press_for_evidence",
        action_candidates=[{"type": "ask", "target": "owner"}],
        success_condition={"type": "belief_threshold",
                           "belief": "mayor_embezzled",
                           "min_confidence": 0.9})
    agent.plan_manager.set_plan(
        agent.state, "press_for_evidence",
        [step, st("observe_response")], 1)
    plan = agent.state.current_plan

    def evaluate(result, tick):
        agent._pending_step_check = {
            "plan_id": plan.plan_id, "step_index": 0,
            "step_id": "press", "action": dict(ask), "tick": tick - 1}
        agent._evaluate_step_from_result(
            {"tick": tick, "events": []}, result, tick)

    evaluate({"ok": False}, 2)
    assert plan.steps[0].attempts == 1 and plan.status == "active"
    evaluate({"ok": False}, 3)
    assert plan.steps[0].attempts == 2 and plan.status == "blocked"
    assert plan.blocked_info["step_id"] == "press"
    assert plan.pending_recovery == {
        "strategy": "replan", "source": "policy_default"}
    kinds = [u["transition_kind"] for u in agent.drain_chain_updates()
             if u["kind"] == "plan_transition"]
    assert "step_evaluated" in kinds and "blocked" in kinds
    # the result path cannot advance a blocked plan...
    evaluate({"ok": True}, 4)
    assert plan.current_step == 0 and plan.status == "blocked"
    # ...and neither can a provider advance (B3 semantics change)
    note = agent.plan_manager.apply_update(
        agent.state, PlanUpdate(status="advance"), "provider", 5)
    assert note == "advance_rejected_blocked"
    assert plan.status == "blocked"
    # the recovery machine is the way out: retry resets the counter
    note = agent.plan_manager.execute_recovery(
        agent.state, 6, "retry", "try once more", source="provider")
    assert note == "recovered_retry"
    assert plan.status == "active" and plan.steps[0].attempts == 0
    # a conditional step ignores provider advance even when active...
    note = agent.plan_manager.apply_update(
        agent.state, PlanUpdate(status="advance"), "provider", 7)
    assert note == "advance_rejected_conditional_step"
    assert plan.current_step == 0
    # ...and advances when its success condition actually holds
    agent.memory.update_belief("mayor_embezzled", 0.95, "inferred",
                               "证据确凿", event_id="E77", tick=8)
    evaluate({"ok": True}, 8)
    assert plan.current_step == 1


def test_commitment_gate_blocks_ordinary_replacement_only():
    agent = _owner_agent()
    agent._set_plan("find_letter_source",
                    [st("find_letter_source"), st("stay_calm")])
    # commitment 0.8 >= gate 0.7: an ordinary event must not displace it
    agent._set_plan("deny_and_observe", [st("deny_and_observe")])
    assert agent.state.current_plan.goal == "find_letter_source"
    kinds = [u["kind"] for u in agent._drain_plan_transitions()]
    assert "replace_blocked_by_commitment" in kinds
    # a critical new-information event does displace it
    agent._set_plan("deny_and_observe", [st("deny_and_observe")],
                    critical=True)
    assert agent.state.current_plan.goal == "deny_and_observe"


def test_same_goal_refresh_is_not_gated():
    agent = _owner_agent()
    agent._set_plan("find_letter_source",
                    [st("find_letter_source"), st("stay_calm")])
    agent._set_plan("find_letter_source", [st("find_letter_source")])
    assert agent.state.current_plan.goal == "find_letter_source"
    assert len(agent.state.current_plan.steps) == 1
    kinds = [u["kind"] for u in agent._drain_plan_transitions()]
    assert "replace_blocked_by_commitment" not in kinds
