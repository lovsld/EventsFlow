"""Plan-layer upgrade tests (2026-11-11).

Covered: result-driven plan advancement — a fulfilled step advances
the plan from the action's RESULT (a fact about the world, judged by
the shared predicate table in v02/plan_steps.py), not from a provider
opinion; the same action signature never counts twice; a plan whose
last step is fulfilled completes; a provider's explicit advance and
the result-driven path never double-count; and the owner's
find_letter_source / find_who_talked steps — which the step-1 audit
found had NO executable behaviour (M1a 0.240) — are now hittable by
the owner's own rule-provider actions.

See PLAN_LAYER_REPORT.md and v02/agent.py::_advance_step_from_result.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from v02.agent import CharacterAgent
from v02.config import load_all
from v02.plan_steps import step_consistent
from v02.protocol import PlanUpdate
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


def _plan(agent: CharacterAgent, goal: str, steps: list[str]):
    plan = agent.plan_manager.set_plan(agent.state, goal, steps, tick=5,
                                       reason="test")
    agent._drain_plan_transitions()   # keep the 'set' record out of the way
    return plan


def _stash(agent: CharacterAgent, plan, step_index: int, action: dict):
    agent._pending_step_check = {
        "plan_id": plan.plan_id, "step_index": step_index,
        "step": plan.steps[step_index], "tick": 5, "action": action,
        "agent_loc": "tables", "owner_loc": "tables"}


def _plan_updates(agent: CharacterAgent) -> list[dict]:
    return [u for u in agent.drain_chain_updates()
            if u.get("kind") == "plan_transition"]


# ---------- result-driven advancement ----------

def test_fulfilled_step_advances_pointer_and_audits():
    agent = make_agent("soldier")
    plan = _plan(agent, "press_for_evidence",
                 ["press_for_evidence", "observe_response"])
    _stash(agent, plan, 0, ASK_OWNER)
    agent._advance_step_from_result({"ok": True}, 6, "soldier:5")
    assert agent.state.current_plan.current_step == 1
    ups = _plan_updates(agent)
    assert len(ups) == 1
    assert ups[0]["transition_kind"] == "advance"
    assert ups[0]["decision_key"] == "soldier:5"
    assert ups[0]["reason"] == "result_driven:press_for_evidence"


def test_unfulfilled_or_failed_result_advances_nothing():
    agent = make_agent("soldier")
    plan = _plan(agent, "press_for_evidence",
                 ["press_for_evidence", "observe_response"])
    # executed but off-step: a work action does not press for evidence
    _stash(agent, plan, 0, {**ASK_OWNER, "type": "work",
                            "claim": None, "topic": "count_coins"})
    agent._advance_step_from_result({"ok": True}, 6, "soldier:5")
    assert agent.state.current_plan.current_step == 0
    # on-step but the world refused it: a fact of failure fulfils nothing
    _stash(agent, plan, 0, ASK_OWNER)
    agent._advance_step_from_result({"ok": False}, 6, "soldier:5")
    assert agent.state.current_plan.current_step == 0
    assert _plan_updates(agent) == []


def test_same_signature_never_counts_twice():
    agent = make_agent("soldier")
    plan = _plan(agent, "press_for_evidence",
                 ["press_for_evidence", "press_for_evidence",
                  "observe_response"])
    _stash(agent, plan, 0, ASK_OWNER)
    agent._advance_step_from_result({"ok": True}, 6, "soldier:5")
    assert agent.state.current_plan.current_step == 1
    # the identical action again for the next step: deduped, not counted
    _stash(agent, plan, 1, ASK_OWNER)
    agent._advance_step_from_result({"ok": True}, 7, "soldier:6")
    assert agent.state.current_plan.current_step == 1
    kinds = [u["transition_kind"] for u in _plan_updates(agent)]
    assert kinds == ["advance", "advance_dedup"]


def test_last_step_fulfilled_completes_the_plan():
    agent = make_agent("soldier")
    _plan(agent, "press_for_evidence", ["press_for_evidence"])
    plan = agent.state.current_plan
    _stash(agent, plan, 0, ASK_OWNER)
    agent._advance_step_from_result({"ok": True}, 6, "soldier:5")
    assert agent.state.current_plan is None      # completed & cleared
    ups = _plan_updates(agent)
    assert ups and ups[0]["transition_kind"] == "advance"
    assert ups[0]["after"]["status"] == "completed"


def test_provider_advance_and_result_advance_never_double_count():
    agent = make_agent("soldier")
    plan = _plan(agent, "press_for_evidence",
                 ["press_for_evidence", "observe_response"])
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
    agent._advance_step_from_result({"ok": True}, 6, "soldier:5")
    assert agent.state.current_plan.current_step == 1
    assert _plan_updates(agent) == []


# ---------- the owner's source-hunt steps are executable ----------

def test_owner_probe_actions_hit_the_predicate():
    ask = {"type": "ask", "target": "merchant", "claim": "letter_question"}
    confront = {"type": "confront", "target": "soldier",
                "claim": "letter_question"}
    assert step_consistent("find_letter_source", ask,
                           agent_loc="bar", owner_loc="bar") is True
    assert step_consistent("find_who_talked", confront,
                           agent_loc="bar", owner_loc="bar") is True


def test_owner_rule_decision_under_find_letter_source_is_consistent():
    agent = make_agent("owner", provider="rule")
    agent._set_plan("find_letter_source", ["find_letter_source", "stay_calm"])
    decision, _record = asyncio.run(
        agent.decide(perc(3, "bar", colocated=["merchant"])))
    action = decision.action.model_dump()
    assert step_consistent("find_letter_source", action,
                           agent_loc="bar", owner_loc="bar") is True


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
    done = db.execute(
        "select count(*) from plan_transitions where kind = 'advance' "
        "and after_json like '%\"status\": \"completed\"%'").fetchone()[0]
    assert done > 0                    # plans now run to completion
    # the owner's source-hunt fires in a real run: he questions someone
    # about the letter (claim letter_question) with his own ask/confront
    log = json.loads((planlayer_run / "world_log.json").read_text())
    probes = [e for rec in log for e in rec["events"]
              if e.get("claim") == "letter_question"
              and (e.get("actors") or [None])[0] == "owner"
              and e["type"] in ("ask", "confront")]
    assert probes
