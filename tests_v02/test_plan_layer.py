"""Plan-layer tests — V0.2-B B3 rewrite (pre-registered disposition #5).

The original file tested the plan-layer upgrade: result-driven
advancement judged by the shared predicate table in
v02/plan_steps.py. B3 deletes that table; a step is now fulfilled
when its declared success_condition holds after the action's result
(v02/plan_evaluator.py). These tests re-express the original
existence assertions equivalently against the B3 machinery: a
fulfilled step advances the pointer and audits; an unfulfilled or
failed result advances nothing (but a counted failure is recorded);
the same action signature never counts twice; a fulfilled last step
completes the plan (with a plan_completed record); a provider
advance (legacy steps only, in B3) and the result path never
double-count; and the owner's source-hunt behaviour is produced by
the synthesized steps' templates.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from v02.agent import CharacterAgent
from v02.config import load_all
from v02.plan_evaluator import action_matches_templates
from v02.protocol import PlanStep, PlanUpdate
from v02.providers import make_provider
from v02.runner import run_simulation

ROOT = Path(__file__).resolve().parent.parent


def make_agent(cid: str, provider: str = "mock") -> CharacterAgent:
    cfg = load_all()[cid]
    return CharacterAgent(cfg, make_provider(provider, cfg, seed=42))


def perc(tick: int, loc: str, events=(), colocated=()) -> dict:
    return {"tick": tick, "time": "21:00", "location": loc,
            "colocated": [{"id": c, "name": c, "role": ""} for c in colocated],
            "events": list(events), "muffled": [], "visible_objects": [],
            "agent_id": "x", "world_public": {"weather": "clear"}}


ASK_OWNER = {"type": "ask", "target": "owner", "target_location": None,
             "object": None, "topic": "investigation_probe",
             "claim": "investigation_probe", "content": "老板，这几天酒馆有没有异常？"}


def press_step(step_id="press"):
    """A conditional step the soldier fulfils by pressing the owner:
    its success is a belief he does NOT yet hold (the mayor
    proposition at 0.55) — the test grants it to simulate the ask's
    testimony landing, because fulfilment is judged on CONDITIONS
    (facts about the agent's state), not on the action's label."""
    return PlanStep(
        step_id=step_id, objective="press_for_evidence",
        action_candidates=[{"type": "ask", "target": "owner"}],
        success_condition={"type": "belief_threshold",
                           "belief": "mayor_embezzled",
                           "min_confidence": 0.55})


def _grant(agent: CharacterAgent, tick: int = 5):
    agent.memory.update_belief("mayor_embezzled", 0.6, "heard",
                               "老板的话坐实了传闻", event_id=f"E{tick}",
                               tick=tick, source="owner")


def _plan(agent: CharacterAgent, goal: str, steps: list):
    plan = agent.plan_manager.set_plan(agent.state, goal, steps, tick=5,
                                       reason="test")
    agent._drain_plan_transitions()   # keep the 'set' record out of the way
    return plan


def _stash(agent: CharacterAgent, plan, step_index: int, action: dict):
    agent._pending_step_check = {
        "plan_id": plan.plan_id, "step_index": step_index,
        "step_id": plan.steps[step_index].step_id, "tick": 5,
        "action": action}


def _evaluate(agent: CharacterAgent, result: dict, tick: int = 6):
    agent._evaluate_step_from_result({"tick": tick, "events": []},
                                     result, tick)


def _plan_updates(agent: CharacterAgent) -> list[dict]:
    return [u for u in agent.drain_chain_updates()
            if u.get("kind") == "plan_transition"]


# ---------- result-driven advancement ----------

def test_fulfilled_step_advances_pointer_and_audits():
    agent = make_agent("soldier")
    plan = _plan(agent, "press_for_evidence",
                 [press_step(), "observe_response"])
    _stash(agent, plan, 0, ASK_OWNER)
    _grant(agent)            # the ask's testimony lands
    _evaluate(agent, {"ok": True})
    assert agent.state.current_plan.current_step == 1
    ups = _plan_updates(agent)
    kinds = [u["transition_kind"] for u in ups]
    assert kinds == ["step_evaluated", "advance"]
    adv = ups[1]
    assert adv["decision_key"] == "soldier:5"
    assert adv["reason"] == "result_driven:press"


def test_unfulfilled_or_failed_result_advances_nothing():
    agent = make_agent("soldier")
    plan = _plan(agent, "press_for_evidence",
                 [press_step(), "observe_response"])
    # executed but off-step: a work action matches no template, so
    # the evaluation counts nothing at all
    _stash(agent, plan, 0, {**ASK_OWNER, "type": "work",
                            "claim": None, "topic": "count_coins"})
    _evaluate(agent, {"ok": True})
    assert agent.state.current_plan.current_step == 0
    assert plan.steps[0].attempts == 0
    # on-step but the world refused it: a counted failure (recorded
    # as a step_evaluated transition with failed=true), no advance
    _stash(agent, plan, 0, ASK_OWNER)
    _evaluate(agent, {"ok": False})
    assert agent.state.current_plan.current_step == 0
    assert plan.steps[0].attempts == 1
    ups = _plan_updates(agent)
    assert [u["transition_kind"] for u in ups] == \
        ["step_evaluated", "step_evaluated"]
    assert ups[1]["after"]["failed"] is True


def test_same_signature_never_counts_twice():
    agent = make_agent("soldier")
    plan = _plan(agent, "press_for_evidence",
                 [press_step("press"), press_step("press_again"),
                  "observe_response"])
    _stash(agent, plan, 0, ASK_OWNER)
    _grant(agent)
    _evaluate(agent, {"ok": True})
    assert agent.state.current_plan.current_step == 1
    # the identical action again for the next step: deduped, not counted
    _stash(agent, plan, 1, ASK_OWNER)
    _evaluate(agent, {"ok": True}, tick=7)
    assert agent.state.current_plan.current_step == 1
    kinds = [u["transition_kind"] for u in _plan_updates(agent)]
    assert kinds == ["step_evaluated", "advance",
                     "step_evaluated", "advance_dedup"]


def test_last_step_fulfilled_completes_the_plan():
    agent = make_agent("soldier")
    plan = _plan(agent, "press_for_evidence", [press_step()])
    _stash(agent, plan, 0, ASK_OWNER)
    _grant(agent)
    _evaluate(agent, {"ok": True})
    assert agent.state.current_plan is None      # completed & cleared
    ups = _plan_updates(agent)
    kinds = [u["transition_kind"] for u in ups]
    assert "advance" in kinds and "plan_completed" in kinds
    done = next(u for u in ups if u["transition_kind"] == "advance")
    assert done["after"]["status"] == "completed"


def test_provider_advance_and_result_advance_never_double_count():
    agent = make_agent("soldier")
    plan = _plan(agent, "press_for_evidence",
                 ["press_for_evidence", "observe_response"])  # legacy
    _stash(agent, plan, 0, ASK_OWNER)
    # the provider's explicit advance fires first (decide time) and
    # moves the pointer; the result path must then find its stashed
    # step gone and add nothing
    note = agent.plan_manager.apply_update(
        agent.state, PlanUpdate(status="advance"),
        "ask:owner:investigation_probe", 5)
    assert note == "advanced"
    assert agent.state.current_plan.current_step == 1
    agent._drain_plan_transitions()
    _evaluate(agent, {"ok": True})
    assert agent.state.current_plan.current_step == 1
    assert _plan_updates(agent) == []


# ---------- the owner's source-hunt steps are executable ----------

def test_owner_synthesized_steps_carry_probe_templates():
    agent = make_agent("owner", provider="rule")
    # activate find_letter_source through its declared condition
    agent.receive(perc(1, "tables", events=[
        {"event_id": "E1", "tick": 1, "type": "ask",
         "actors": ["soldier", "owner"], "location": "tables",
         "claim": "letter_question", "topic": "letter_question",
         "text": "…"}]), None)
    goal = next(g for g in agent.state.goals
                if g.key == "find_letter_source")
    assert goal.status == "active"
    from v02.conditions import build_view
    steps = agent.provider.synthesize_steps(goal, build_view(agent, 1))
    templates = [t for s in steps for t in s.action_candidates]
    assert any(t.get("type") in ("ask", "confront") for t in templates)


def test_owner_rule_decision_under_find_letter_source_is_consistent():
    agent = make_agent("owner", provider="rule")
    agent.receive(perc(1, "tables", events=[
        {"event_id": "E1", "tick": 1, "type": "ask",
         "actors": ["soldier", "owner"], "location": "tables",
         "claim": "letter_question", "topic": "letter_question",
         "text": "…"}]), None)
    goal = next(g for g in agent.state.goals
                if g.key == "find_letter_source")
    from v02.conditions import build_view
    steps = agent.provider.synthesize_steps(goal, build_view(agent, 1))
    agent.plan_manager.set_plan(agent.state, "find_letter_source",
                                steps, tick=2, goal_id=goal.key)
    decision, _record = asyncio.run(
        agent.decide(perc(3, "bar", colocated=["merchant"])))
    action = decision.action.model_dump()
    plan = agent.state.current_plan
    current = plan.steps[plan.current_step] if plan else steps[0]
    assert action_matches_templates(action, current.action_candidates) \
        or decision.plan_update.status in ("replace", "continue")


# ---------- integration: a real rule run advances plans ----------

@pytest.fixture(scope="module")
def planlayer_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    return asyncio.run(run_simulation(mode="rule", ticks=20, seed=42,
                                      run_name="rule_planlayer_test",
                                      out_root=out))


def test_real_run_advances_plans_from_results(planlayer_run):
    db = sqlite3.connect(f"file:{planlayer_run}/acp.sqlite3?mode=ro",
                         uri=True)
    n = db.execute(
        "select count(*) from plan_transitions where kind = 'advance' "
        "and reason like 'result_driven:%'").fetchone()[0]
    assert n > 0                       # pre-upgrade this was exactly 0
    evals = db.execute(
        "select count(*) from plan_transitions "
        "where kind = 'step_evaluated'").fetchone()[0]
    assert evals >= n                  # every advance was judged first
    fulfilled = db.execute(
        "select count(*) from plan_transitions "
        "where kind = 'step_evaluated' "
        "and after_json like '%\"fulfilled\": true%'").fetchone()[0]
    assert fulfilled >= n
    # B3 re-expression (disposition #5, reported in V0.2-B_REPORT.md):
    # the original also asserted plan completions and owner
    # letter_question probes in a tavern run. Under condition
    # semantics the tavern's plans more often BLOCK and replan than
    # complete inside 20 ticks (their step conditions are the goals'
    # own hard leaves), and the owner's source-hunt never ignites
    # because no letter_question is ever asked in the generic
    # dynamics — both are trajectory facts of the new mechanics, not
    # machinery failures. Plan-completion-in-a-real-run evidence is
    # carried by the B3 warehouse gate trajectory instead; the
    # blocked/recovery machinery firing here is asserted as the
    # tavern-side existence claim.
    blocked = db.execute(
        "select count(*) from plan_transitions "
        "where kind = 'blocked'").fetchone()[0]
    # V0.2-C C4 adjudication (pre-registered disposition C4-10,
    # registered in docs/V0.2-C_IMPLEMENTATION_REPORT.md): the
    # blocked > 0 existence claim is REMOVED, and what replaces it
    # is the pair of facts C4's design makes true of this run.
    # The tavern blocks this assertion sampled were the pathology
    # C4 exists to fix: under the B-era failure rule (c), ANY leaf
    # at satisfaction 0 — belief and event leaves included — made
    # a step "decisively false", so tavern plans blocked on
    # not-yet-obtained beliefs. Design 5.2 narrows decisive
    # falsity to object/location-class leaves; the tavern's step
    # conditions are belief/event-class, so a tavern plan can no
    # longer false-block: blocked == 0 here (and in all five
    # seed-group runs under C4). Blocked/recovery existence
    # evidence relocates to where object-class failures
    # legitimately occur — the warehouse gate trajectories and
    # the C4 unit fixtures — the same relocation the B3 comment
    # above already accepted for plan completions. The tavern-side
    # positive claim becomes the C4 stage gate's own quantity:
    # the Step Fulfillment Rate (design 9) must sit far above the
    # B-era canonical baseline of 0.028 (C4 canonical measures
    # 23/46 = 0.500).
    assert blocked == 0
    assert fulfilled * 10 > evals      # SFR > 0.1, vs B-era 0.028
