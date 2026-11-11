"""V0.2-B B3 tests: the result-driven Plan Engine.

The StepEvaluator's four-layer separation (the review's P0 as a
named regression), the consecutive-failure semantics of design 5.4,
the recovery state machine of 5.5 (legality table + policy default
+ retry cap), the environment reset, legacy-step discipline, and
the shared LLM contract text.
"""
from __future__ import annotations

import json

from v02 import conditions
from v02.agent import CharacterAgent
from v02.config import load_all
from v02.plan_evaluator import evaluate_step
from v02.protocol import PlanStep, PlanUpdate, RecoverySpec
from v02.providers.rule_provider import RuleProvider
from v02.scenario import load_scenario

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VARIANT_ROOT = ROOT / "characters_variant"
WAREHOUSE = json.loads(
    (ROOT / "scenarios" / "warehouse_key_world.json").read_text(encoding="utf-8"))

SEARCH = {"type": "search", "target": None, "target_location": "door",
          "object": None, "claim": None, "topic": None}


def keeper_agent():
    cfg = load_all(VARIANT_ROOT)["keeper"]
    scenario = load_scenario(WAREHOUSE)
    provider = RuleProvider(cfg, seed=42)
    provider.scenario = scenario
    agent = CharacterAgent(cfg, provider, scenario=scenario)
    agent.evaluate_goals(0, cause="init")
    return agent


def find_step():
    return PlanStep(
        step_id="locate:warehouse_key", objective="locate warehouse_key",
        action_candidates=[{"type": "search"}],
        success_condition={"type": "object_found",
                           "object": "warehouse_key"})


def blocked_keeper():
    """A keeper whose locate step has failed twice at wrong places:
    plan blocked, policy default (retry) scheduled."""
    agent = keeper_agent()
    pm = agent.plan_manager
    pm.set_plan(agent.state, "find_missing_key",
                [find_step(), PlanStep(
                    step_id="secure:warehouse_key",
                    objective="secure warehouse_key",
                    action_candidates=[{"type": "search"}],
                    success_condition={"type": "object_possession",
                                       "object": "warehouse_key",
                                       "holder": "self"})],
                1, goal_id="find_missing_key")
    plan = agent.state.current_plan
    for tick in (2, 3):
        agent._pending_step_check = {
            "plan_id": plan.plan_id, "step_index": 0,
            "step_id": "locate:warehouse_key", "tick": tick - 1,
            "action": dict(SEARCH)}
        agent._evaluate_step_from_result(
            {"tick": tick, "events": []}, {"ok": True, "found": []}, tick)
    assert plan.status == "blocked"
    return agent, plan


# ---------------- the P0 regression (named) ----------------

def test_p0_search_ok_found_nothing_is_not_step_fulfilment():
    """THE P0 of the external review, as a regression test: the
    World Engine reports the search action as successful
    (ok=True) with found=[] — under the old label table the step
    counted as fulfilled anyway. Under the StepEvaluator the step
    is NOT fulfilled, and the evaluation counts exactly ONE
    consecutive failure."""
    agent = keeper_agent()
    view = conditions.build_view(agent, 1)
    ev = evaluate_step(find_step(), action=dict(SEARCH),
                       action_result={"ok": True, "found": []}, view=view)
    assert ev.fulfilled is False
    assert ev.failed is True
    assert ev.consistent is True
    # and once the key is actually in his subjective world, the same
    # evaluation fulfils the step
    agent.state.found_objects.append("warehouse_key")
    view = conditions.build_view(agent, 2)
    ev = evaluate_step(find_step(), action=dict(SEARCH),
                       action_result={"ok": True,
                                      "found": ["warehouse_key"]},
                       view=view)
    assert ev.fulfilled is True and ev.failed is False


def test_undecidable_evaluation_counts_nothing():
    agent = keeper_agent()
    step = PlanStep(
        step_id="gather", objective="gather",
        action_candidates=[{"type": "ask"}],
        success_condition={"type": "belief_threshold",
                           "belief": "key_missing_this_morning",
                           "min_confidence": 0.99})
    view = conditions.build_view(agent, 1)
    # belief is 0.95: satisfaction 0.95/0.99 is neither 1 nor
    # decisively 0, no failure condition, result ok -> undecidable
    ev = evaluate_step(step, action={"type": "ask", "target": "keeper"},
                       action_result={"ok": True}, view=view)
    assert ev.fulfilled is False and ev.failed is False
    # an off-template action is not even consistent: also no count
    ev = evaluate_step(step, action={"type": "work"},
                       action_result={"ok": False}, view=view)
    assert ev.consistent is False and ev.failed is False


def test_blocked_entry_and_policy_default_scheduling():
    agent, plan = blocked_keeper()
    assert plan.steps[0].attempts == 2
    assert plan.blocked_info["step_id"] == "locate:warehouse_key"
    # the protocol's default on_exhausted is "replan"
    assert plan.pending_recovery["strategy"] == "replan"
    kinds = [u["transition_kind"] for u in agent.drain_chain_updates()
             if u["kind"] == "plan_transition"]
    assert kinds.count("step_evaluated") == 2 and "blocked" in kinds


def test_environment_change_resets_the_failure_counter():
    agent = keeper_agent()
    pm = agent.plan_manager
    pm.set_plan(agent.state, "find_missing_key", [find_step()], 1,
                goal_id="find_missing_key")
    plan = agent.state.current_plan
    agent._pending_step_check = {
        "plan_id": plan.plan_id, "step_index": 0,
        "step_id": "locate:warehouse_key", "tick": 1,
        "action": dict(SEARCH)}
    agent._evaluate_step_from_result({"tick": 2, "events": []},
                                     {"ok": True, "found": []}, 2)
    assert plan.steps[0].attempts == 1
    # a perceived event naming the step's object resets the counter
    agent._pending_step_check = {
        "plan_id": plan.plan_id, "step_index": 0,
        "step_id": "locate:warehouse_key", "tick": 2,
        "action": dict(SEARCH)}
    agent._evaluate_step_from_result(
        {"tick": 3, "events": [
            {"event_id": "E9", "tick": 3, "type": "speak",
             "actors": ["traveler"], "location": "tables",
             "object": "warehouse_key"}]},
        {"ok": True, "found": []}, 3)
    kinds = [u["transition_kind"] for u in agent.drain_chain_updates()
             if u["kind"] == "plan_transition"]
    assert "environment_reset" in kinds
    # reset to 0, then THIS evaluation's failure counts once
    assert plan.steps[0].attempts == 1


# ---------------- recovery state machine ----------------

def test_policy_default_recovery_executes_without_provider():
    # a step whose policy default is retry: the agent decides with a
    # bare continue and the runtime executes the scheduled default
    # by itself — blocked no longer depends on the provider
    # speaking up
    from v02.protocol import AgentDecision, RetryPolicy
    agent = keeper_agent()
    step = find_step()
    step.retry_policy = RetryPolicy(max_attempts=2, on_exhausted="retry")
    pm = agent.plan_manager
    pm.set_plan(agent.state, "find_missing_key", [step], 1,
                goal_id="find_missing_key")
    plan = agent.state.current_plan
    for tick in (2, 3):
        agent._pending_step_check = {
            "plan_id": plan.plan_id, "step_index": 0,
            "step_id": "locate:warehouse_key", "tick": tick - 1,
            "action": dict(SEARCH)}
        agent._evaluate_step_from_result(
            {"tick": tick, "events": []}, {"ok": True, "found": []}, tick)
    assert plan.status == "blocked"
    assert plan.pending_recovery["strategy"] == "retry"
    agent._tick = 4
    decision = AgentDecision(
        intent="wait", action={"type": "wait"},
        reasoning_summary="x", expected_outcome="y", confidence=0.5,
        plan_update=PlanUpdate(status="continue"))
    note = agent._apply_plan_update(decision)
    assert note == "recovered_retry"
    assert plan.status == "active" and plan.steps[0].attempts == 0
    kinds = [t["kind"] for t in agent.plan_manager.transitions]
    assert "recovery" in kinds


def test_retry_cap_is_two_per_step():
    agent, plan = blocked_keeper()
    pm = agent.plan_manager
    for _ in range(2):
        assert pm.execute_recovery(agent.state, 5, "retry", "again",
                                   source="provider") == "recovered_retry"
        # force back into blocked by hand for the next round
        plan.status = "blocked"
        plan.steps[0].attempts = 2
    note = pm.execute_recovery(agent.state, 6, "retry", "third",
                               source="provider")
    assert note == "recovery_rejected"
    assert plan.status == "blocked"


def test_skip_advances_and_completion_counts_skips():
    agent, plan = blocked_keeper()
    pm = agent.plan_manager
    note = pm.execute_recovery(agent.state, 5, "skip", "hopeless here",
                               source="provider")
    assert note == "recovered_skip"
    assert plan.steps[0].status == "skipped"
    assert plan.current_step == 1 and plan.status == "active"
    # fulfilling the last step completes the plan; the completion
    # record carries the skipped count
    agent.state.holdings.append("warehouse_key")
    agent._pending_step_check = {
        "plan_id": plan.plan_id, "step_index": 1,
        "step_id": "secure:warehouse_key", "tick": 5,
        "action": dict(SEARCH)}
    agent._evaluate_step_from_result({"tick": 6, "events": []},
                                     {"ok": True, "found": []}, 6)
    assert agent.state.current_plan is None
    kinds = [(u["transition_kind"], u.get("reason") or "")
             for u in agent.drain_chain_updates()
             if u["kind"] == "plan_transition"]
    completed = [r for k, r in kinds if k == "plan_completed"]
    assert completed and "skipped=1" in completed[0]


def test_suspend_requires_condition_and_reactivates_via_evaluator():
    agent, plan = blocked_keeper()
    pm = agent.plan_manager
    note = pm.execute_recovery(agent.state, 5, "suspend", "wait it out",
                               source="provider")
    assert note == "recovery_rejected"          # no condition given
    note = pm.execute_recovery(
        agent.state, 5, "suspend", "until I hold it", source="provider",
        reactivate_when={"type": "object_possession",
                         "object": "warehouse_key", "holder": "self"})
    assert note == "recovered_suspend"
    assert plan.status == "suspended"
    agent.evaluate_goals(6, cause="perception")
    assert plan.status == "suspended"           # condition unmet
    agent.state.holdings.append("warehouse_key")
    agent.evaluate_goals(7, cause="perception")
    assert plan.status == "active"              # reactivated


def test_abandon_recovery_leaves_the_goal_alone():
    agent, plan = blocked_keeper()
    pm = agent.plan_manager
    note = pm.execute_recovery(agent.state, 5, "abandon", None,
                               source="provider")
    assert note == "recovery_rejected"          # reason required
    note = pm.execute_recovery(agent.state, 5, "abandon", "give up",
                               source="provider")
    assert note == "recovered_abandon"
    assert agent.state.current_plan is None
    # the goal is NOT abandoned with the plan (four-layer separation)
    goal = next(g for g in agent.state.goals
                if g.key == "find_missing_key")
    assert goal.status == "active"


def test_replan_recovery_inherits_the_goal_link():
    agent, plan = blocked_keeper()
    pm = agent.plan_manager
    note = pm.execute_recovery(agent.state, 5, "replan", "fresh eyes",
                               source="provider")
    assert note == "recovery_deferred"          # no steps supplied
    note = pm.execute_recovery(agent.state, 5, "replan", "fresh eyes",
                               source="provider",
                               new_steps=[find_step()])
    assert note == "recovered_replan"
    new_plan = agent.state.current_plan
    assert new_plan is not None and new_plan.plan_id != plan.plan_id
    assert new_plan.goal_id == "find_missing_key"
    kinds = [(t["kind"], t.get("reason") or "") for t in pm.transitions]
    assert any(k == "abandon" and r == "replanned" for k, r in kinds)


def test_provider_recovery_preempts_the_policy_default():
    agent, plan = blocked_keeper()
    agent._tick = 5
    from v02.protocol import AgentDecision
    decision = AgentDecision(
        intent="wait", action={"type": "wait"},
        reasoning_summary="x", expected_outcome="y", confidence=0.5,
        plan_update=PlanUpdate(
            status="continue",
            recovery=RecoverySpec(strategy="skip",
                                  reason="provider chooses skip")))
    note = agent._apply_plan_update(decision)
    assert note == "recovered_skip"             # not the default retry
    assert plan.steps[0].status == "skipped"


# ---------------- legacy discipline + contract ----------------

def test_legacy_step_is_never_judged_and_never_blocks():
    agent = keeper_agent()
    legacy = PlanStep(step_id="legacy:0", objective="free text step")
    view = conditions.build_view(agent, 1)
    ev = evaluate_step(legacy, action={"type": "wait"},
                       action_result={"ok": False}, view=view)
    assert ev.legacy and not ev.fulfilled and not ev.failed


def test_llm_contract_text_is_shared_and_documents_b3():
    from v02.providers.gemini_provider import GeminiProvider
    from v02.providers.openai_provider import (_JSON_CONTRACT,
                                               OpenAICompatibleProvider)
    cfg = load_all()["owner"]
    gemini = GeminiProvider(cfg)
    text = gemini._system_prompt()
    assert _JSON_CONTRACT in text
    for needle in ("selected_goal_id", "trust", "主观认知",
                   "recovery", "goal_id", "blocked"):
        assert needle in _JSON_CONTRACT
    # the OpenAI-compatible provider presents the same contract
    import inspect
    src = inspect.getsource(OpenAICompatibleProvider._system_prompt)
    assert "_JSON_CONTRACT" in src
