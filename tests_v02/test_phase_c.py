"""Phase C tests for ACP V0.2 (pytest).

Covered: the Plan Manager (design 12.5 plan persistence: multi-step
plans advance across ticks, repeating the same action does not count
a step twice, commitment decay is quantifiable, reason-gating rejects
unreasoned abandon/replace), the Prediction Engine + Belief Manager
(a falsified prediction moves belief/trust by the exact rule-table
deltas; a hit moves nothing), memory consolidation's
prediction-error-linked importance scoring, and full cognitive-chain
reconstruction of a single decision from a real run's SQLite alone.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from v02.agent import CharacterAgent
from v02.config import load_all
from v02.persistence import DecisionRow, PredictionRow, Store
from v02.protocol import (AgentDecision, DecisionContext, PlanUpdate,
                          Prediction, PredictionError)
from v02.providers import make_provider
from v02.runner import run_simulation

ROOT = Path(__file__).resolve().parent.parent


def make_agent(cid: str) -> CharacterAgent:
    cfg = load_all()[cid]
    return CharacterAgent(cfg, make_provider("mock", cfg, seed=42))


def perc(tick: int, loc: str, events=(), colocated=()) -> dict:
    return {"tick": tick, "time": "21:00", "location": loc,
            "colocated": [{"id": c, "name": c, "role": ""} for c in colocated],
            "events": list(events), "muffled": [], "visible_objects": [],
            "agent_id": "x", "world_public": {"weather": "clear"}}


def speech_prediction(agent: CharacterAgent, verb: str, target: str,
                      trust: float = 0.3, tick: int = 5) -> Prediction:
    ctx = DecisionContext(
        character_id=agent.config.character_id, tick=tick,
        current_time="21:25", location="tables",
        perception={"colocated": [{"id": target}], "events": [], "muffled": []},
        active_goals=[], beliefs=[], emotional_state={}, available_actions=[],
        trust={target: trust})
    decision = AgentDecision(
        intent=verb, action={"type": verb, "target": target, "content": "……"})
    return agent.prediction_engine.predict(agent, ctx, decision)


# ---------- (a) Plan Manager: persistence, dedup, decay, gating ----------
def test_plan_steps_advance_dedup_and_complete():
    agent = make_agent("soldier")
    pm = agent.plan_manager
    pm.set_plan(agent.state, "investigate", ["go_bar", "talk", "report"], tick=0)
    plan = agent.state.current_plan
    assert plan.current_step == 0 and plan.commitment == 0.8

    note = pm.apply_update(agent.state, PlanUpdate(status="advance"), "move:bar", 1)
    assert note == "advanced" and agent.state.current_plan.current_step == 1

    # the SAME action again: the V0.1 oscillation — must not count twice
    note = pm.apply_update(agent.state, PlanUpdate(status="advance"), "move:bar", 2)
    assert note == "advance_dedup" and agent.state.current_plan.current_step == 1
    dedup = [t for t in pm.transitions if t["kind"] == "advance_dedup"]
    assert len(dedup) == 1 and dedup[0]["plan_id"] == plan.plan_id

    note = pm.apply_update(agent.state, PlanUpdate(status="advance"), "speak:owner", 3)
    assert note == "advanced" and agent.state.current_plan.current_step == 2
    note = pm.apply_update(agent.state, PlanUpdate(status="advance"), "listen", 4)
    assert note == "advanced" and agent.state.current_plan is None  # completed
    kinds = [t["kind"] for t in pm.transitions]
    assert kinds.count("advance") == 3


def test_plan_commitment_decay_is_quantified_and_dedup_is_not_progress():
    agent = make_agent("merchant")
    pm = agent.plan_manager
    pm.set_plan(agent.state, "probe", ["step_a", "step_b"], tick=0)
    for t in range(1, 6):
        pm.reconcile(agent.state, t)
    # exactly 0.8 - 5 * 0.02, one decay transition per tick
    assert agent.state.current_plan.commitment == 0.7
    decays = [t for t in pm.transitions if t["kind"] == "commitment_decay"]
    assert len(decays) == 5 and decays[-1]["after"] == 0.7

    agent2 = make_agent("merchant")
    pm2 = agent2.plan_manager
    pm2.set_plan(agent2.state, "probe", ["step_a", "step_b"], tick=0)
    pm2.apply_update(agent2.state, PlanUpdate(status="advance"), "move:bar", 1)
    assert agent2.state.current_plan.commitment == 0.85  # progress bonus
    pm2.apply_update(agent2.state, PlanUpdate(status="advance"), "move:bar", 2)
    pm2.apply_update(agent2.state, PlanUpdate(status="advance"), "move:bar", 3)
    pm2.reconcile(agent2.state, 4)
    # dedup'd repeats were not progress: decay runs from tick 1 -> 0.85-3*0.02
    assert agent2.state.current_plan.commitment == 0.79


def test_plan_reason_gating_rejects_and_records():
    agent = make_agent("owner")
    pm = agent.plan_manager
    agent._tick = 2
    agent._set_plan("deny_and_observe", ["deny", "watch"])
    plan = agent.state.current_plan
    note = pm.apply_update(agent.state, PlanUpdate(status="abandon"),
                           "wait:", agent._tick)
    assert note == "abandon_rejected_no_reason"
    assert agent.state.current_plan is plan
    assert plan.interrupt_log[-1]["kind"] == "abandon_rejected_no_reason"
    note = pm.apply_update(agent.state, PlanUpdate(status="replace"),
                           "wait:", agent._tick)
    assert note == "replace_rejected_no_reason"
    note = pm.apply_update(agent.state,
                           PlanUpdate(status="replace", reason="new_evidence",
                                      new_goal="assess_damage",
                                      new_steps=["assess"]),
                           "wait:", agent._tick)
    assert note == "replaced"
    assert agent.state.current_plan.goal == "assess_damage"
    kinds = [t["kind"] for t in pm.transitions]
    assert "abandon_rejected_no_reason" in kinds and "replace" in kinds


def test_plan_persists_across_decisions_and_step_is_in_context():
    agent = make_agent("soldier")
    agent._tick = 0
    agent._set_plan("investigate", ["go_bar", "talk", "report"])
    for tick in (0, 1):
        decision, record = asyncio.run(agent.decide(perc(tick, "door")))
        assert decision is not None and record["error"] is None
        assert agent.state.current_plan is not None
        assert agent.state.current_plan.goal == "investigate"
        step = getattr(agent.last_context, "plan_step")
        assert step["goal"] == "investigate" and step["step_index"] == 0
        assert step["step"] == "go_bar"
        assert record["prediction"]["action_type"] == decision.action.type
        assert record["decision_key"] == f"soldier:{tick}"


# ---------- Prediction Engine structure ----------
def test_prediction_is_structured_and_deterministic():
    agent = make_agent("soldier")
    p1 = speech_prediction(agent, "ask", "owner")
    p2 = speech_prediction(agent, "ask", "owner")
    assert p1.model_dump() == p2.model_dump()
    assert p1.target_present is True and p1.action_success is True
    assert p1.refusal_expected is False      # trust 0.3 is not below 0.25
    assert p1.new_information_expected is True
    assert p1.found_expected is None
    # every field is a checkable scalar — no free-text "reasoning" anywhere
    for k, v in p1.model_dump().items():
        if k != "basis":
            assert v is None or isinstance(v, (str, int, bool)), k


# ---------- (b) prediction error -> belief/trust ----------
def test_prediction_error_target_left_moves_trust_and_suspicion():
    agent = make_agent("soldier")
    pred = speech_prediction(agent, "speak", "merchant")
    agent.pending_prediction = pred
    agent.receive(perc(6, "tables"), {"ok": False, "reason": "target_left"})
    # rule table: trust -0.05, suspicion +0.05, no double action_success hit
    assert agent.memory.trust_in("merchant") == 0.25
    assert agent.state.emotions["suspicion"] == 0.35
    ups = agent.drain_chain_updates()
    err = next(u for u in ups if u["kind"] == "prediction_error")["error"]
    assert set(err["mismatches"]) == {"target_present", "action_success",
                                      "new_information"}
    # every checked field missed (0.4+0.3+0.1 of 0.8 checked weight)
    assert err["surprise"] == 1.0
    assert err["trust_effects"] == [
        {"other": "merchant", "before": 0.3, "after": 0.25,
         "delta": -0.05, "cause": "target_present_miss"}]
    changes = [u for u in ups if u["kind"] == "belief_change"]
    assert {(c["key"], c["before"], c["after"]) for c in changes} == \
        {("trust:merchant", 0.3, 0.25)}
    assert any(u["kind"] == "consolidation" for u in ups)
    assert agent.pending_prediction is None and agent.last_error is not None


def test_prediction_error_unexpected_refusal_creates_belief():
    agent = make_agent("soldier")
    pred = speech_prediction(agent, "ask", "owner")
    assert pred.refusal_expected is False
    agent.pending_prediction = pred
    refuse = {"event_id": "E900", "tick": 6, "type": "refuse",
              "actors": ["owner"], "location": "tables", "topic": "funds"}
    agent.receive(perc(6, "tables", events=[refuse]), {"ok": True})
    # the prediction-error rule fired on the chain record: -0.08, and a
    # new inferred belief about the owner's cooperativeness was created
    ups = agent.drain_chain_updates()
    changes = {(u["key"]): u for u in ups if u["kind"] == "belief_change"}
    assert changes["trust:owner"]["before"] == 0.3
    assert changes["trust:owner"]["after"] == 0.22
    assert changes["owner_cooperative"]["before"] is None
    assert changes["owner_cooperative"]["after"] == 0.25
    belief = agent.memory.belief("owner_cooperative")
    assert belief is not None and belief.confidence == 0.25
    assert belief.kind == "inferred"
    # (perceiving the refusal itself additionally costs the V0.1 -0.05)
    assert agent.memory.trust_in("owner") == 0.17


def test_prediction_hit_changes_nothing():
    agent = make_agent("soldier")
    pred = speech_prediction(agent, "speak", "merchant")
    agent.pending_prediction = pred
    reply = {"event_id": "E901", "tick": 6, "type": "speak",
             "actors": ["merchant"], "location": "tables",
             "text": "米拉：好啊，慢慢说。", "topic": "greeting"}
    agent.receive(perc(6, "tables", events=[reply]),
                  {"ok": True, "event_id": "E900"})
    err = agent.last_error
    assert err.mismatches == [] and err.surprise == 0.0
    assert err.belief_effects == [] and err.trust_effects == []
    assert agent.memory.trust_in("merchant") == 0.3
    ups = agent.drain_chain_updates()
    assert not any(u["kind"] == "belief_change" for u in ups)
    assert agent.memory.belief("merchant_cooperative") is None


# ---------- memory consolidation: importance is error-linked ----------
def test_consolidation_importance_depends_on_surprise():
    agent = make_agent("owner")
    agent.memory.remember(3, "experienced", "亲历了一件事", "E100")
    agent.memory.remember(3, "inferred", "自己的推测", None)
    pred = Prediction(prediction_id="pred_owner_3", character_id="owner",
                      tick=3, action_type="observe")
    err = PredictionError(prediction_id="pred_owner_3", character_id="owner",
                          decision_tick=3, evaluated_tick=4, surprise=1.0)
    rec = agent.memory_consolidator.consolidate(agent, pred, err, 4)
    by_text = {e["text"]: e for e in rec["entries"]}
    assert by_text["亲历了一件事"]["importance"] == 1.0      # 0.5 + 0.5*1.0
    assert by_text["亲历了一件事"]["consolidated"] is True
    assert by_text["自己的推测"]["importance"] == 0.75       # 0.25 + 0.5*1.0
    assert len(agent.state.long_term_memory) == 2

    calm = make_agent("owner")
    calm.memory.remember(3, "experienced", "平平无奇的一件事", "E101")
    err0 = PredictionError(prediction_id="pred_owner_3", character_id="owner",
                           decision_tick=3, evaluated_tick=4, surprise=0.0)
    rec0 = calm.memory_consolidator.consolidate(calm, pred, err0, 4)
    assert rec0["entries"][0]["importance"] == 0.5           # base only
    assert rec0["consolidated"] == [] and calm.state.long_term_memory == []


# ---------- (c) full-chain reconstruction from a real run's DB ----------
@pytest.fixture(scope="module")
def phasec_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    run_dir = asyncio.run(run_simulation(mode="rule", ticks=12, seed=42,
                                         run_name="rule_phasec_test",
                                         out_root=out))
    return run_dir


def test_db_full_chain_reconstruction(phasec_run):
    store = Store("rule_phasec_test",
                  sqlite_path=str(phasec_run / "acp.sqlite3"))
    counts = store.counts()
    assert counts["decisions"] == 36
    # one prediction and one consolidation per decision, all evaluated
    assert counts["predictions"] == 36 and counts["consolidations"] == 36
    assert counts["plan_transitions"] > 36  # set/continue/decay across agents

    with store.Session() as s:
        row = (s.query(DecisionRow).filter_by(character_id="soldier")
               .order_by(DecisionRow.tick).offset(2).first())
        decision_id, tick = row.id, row.tick
        falsified = [p for p in s.query(PredictionRow).all()
                     if p.error_json and json.loads(p.error_json)["mismatches"]]
        assert falsified, "expected at least one falsified prediction in 12 ticks"

    chain = store.chain_for(decision_id)
    assert chain["decision_key"] == f"soldier:{tick}"
    # 1 context snapshot
    assert chain["context"]["character_id"] == "soldier"
    assert chain["context"]["tick"] == tick
    assert isinstance(chain["context"]["beliefs"], list)
    # 2 prediction (with its deterministic basis snapshot)
    pred = chain["prediction"]
    assert pred is not None and pred["character_id"] == "soldier"
    assert isinstance(pred["basis"]["perceived_ids"], list)
    # 3 decision + 4 execution result
    assert chain["decision"]["action"]["type"] == pred["action_type"]
    assert chain["execution_result"] is not None
    assert "ok" in chain["execution_result"]
    # 5 prediction error (evaluated when the result came back)
    err = chain["prediction_error"]
    assert err is not None and err["decision_tick"] == tick
    assert isinstance(err["mismatches"], list) and 0.0 <= err["surprise"] <= 1.0
    # 6 belief changes caused by it (list; empty iff the prediction hit)
    assert isinstance(chain["belief_changes"], list)
    for c in chain["belief_changes"]:
        assert c["after"] is not None and c["cause"]
    # 7 plan transitions of this decision (reconcile/apply always log)
    assert len(chain["plan_transitions"]) >= 1
    assert all(t["kind"] for t in chain["plan_transitions"])
    # 8 memory consolidation record with scored entries
    cons = chain["consolidation"]
    assert cons is not None and isinstance(cons["entries"], list)
    assert cons["surprise"] == err["surprise"]
