"""V0.2-C C2 tests: the Communication Candidate Generator.

Pure-function level first (ignorance tiers, the generator's
obligation / clarify / veto / duty paths), then the queue's
lifecycle in the agent runtime (addressed-only enqueue, TTL
expiry with audit, dequeue on response, checkpoint migration),
then census persistence on a short real run. Trajectory-level
gates live in the C2 stage record
(docs/V0.2-C_IMPLEMENTATION_REPORT.md).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from v02.comm_candidates import (applicable_duties, effective_queue,
                                 generate_comm_candidates)
from v02.conditions import SubjectiveView
from v02.config import load_all
from v02.info_value import ignorance
from v02.protocol import Belief, CharacterState, Goal
from v02.scenario import default_scenario, load_scenario

ROOT = Path(__file__).resolve().parent.parent
CLINIC_ROOT = ROOT / "characters_scenario_c"
CLINIC_WORLD = ROOT / "scenarios" / "scenario_c_clinic.json"

SC = default_scenario()


def _view(agent_id="owner", beliefs=None, perceived=(), own=(),
          tick=5):
    return SubjectiveView(
        agent_id=agent_id, tick=tick, location="bar",
        beliefs={k: Belief(key=k, confidence=c, kind=kind)
                 for k, (c, kind) in (beliefs or {}).items()},
        perceived_events=list(perceived), own_actions=list(own))


def _gen(view, *, config, goals=(), queue=(), colocated=("soldier",),
         provider=None):
    if provider is None:
        from v02.providers.rule_provider import RuleProvider
        provider = RuleProvider(config, seed=42)
        provider.scenario = SC
    return generate_comm_candidates(
        view=view, scenario=SC, config=config, goals=list(goals),
        pending_questions=list(queue), colocated=list(colocated),
        counts={}, tick=view.tick,
        line_fn=lambda claim, c: "line",
        assert_claim_for=lambda prop: provider._assert_claim_for(
            view, prop))


def _kinds(result):
    return [c["comm_kind"] for c in result["candidates"]]


# ---------------- ignorance (design 3.4) ----------------

def test_ignorance_tiers_and_latest_wins():
    asker = {"tick": 1, "type": "ask", "actor": "soldier",
             "claim": "letter_question"}
    teller = {"tick": 2, "type": "speak", "actor": "soldier",
              "claim": "letter_discussion"}
    v = _view(perceived=[asker])
    assert ignorance(v, "soldier", "anonymous_letter_exists", SC) == 1.0
    # the same person later ASSERTING the proposition: known now
    v2 = _view(perceived=[asker, teller])
    assert ignorance(v2, "soldier", "anonymous_letter_exists",
                     SC) == 0.0
    # no record at all: the neutral prior
    assert ignorance(_view(), "soldier", "anonymous_letter_exists",
                     SC) == 0.5
    # another person's acts say nothing about the soldier
    v3 = _view(perceived=[{**asker, "actor": "merchant"}])
    assert ignorance(v3, "soldier", "anonymous_letter_exists",
                     SC) == 0.5


# ---------------- the answer obligation (design 3.3) ----------------

def _owner_config():
    return load_all()["owner"]


def _letter_queue(via="ask", tick_asked=4):
    return [{"asker": "soldier", "claim_key": "letter_question",
             "proposition": "anonymous_letter_exists",
             "tick_asked": tick_asked, "addressed_to_me": True,
             "via": via}]


def test_answer_obligation_generates_answer_deny_withhold():
    view = _view(beliefs={"anonymous_letter_exists": (1.0, "experienced")})
    res = _gen(view, config=_owner_config(), queue=_letter_queue())
    kinds = _kinds(res)
    assert "answer" in kinds and "deny" in kinds \
        and "withhold" in kinds
    ans = next(c for c in res["candidates"]
               if c["comm_kind"] == "answer")
    assert ans["obligation"] is True
    assert ans["info_override"] == 1.0     # asked by name (3.3(1))
    assert ans["action"]["target"] == "soldier"


def test_confront_entry_gets_deny_but_no_answer():
    # interpretation registered in the C2 stage report: an
    # accusation is answered by denial/refusal/clarification,
    # not by volunteering the asserted account
    view = _view(beliefs={"anonymous_letter_exists": (1.0, "experienced")})
    res = _gen(view, config=_owner_config(),
               queue=_letter_queue(via="confront"))
    kinds = _kinds(res)
    assert "answer" not in kinds
    assert "deny" in kinds and "withhold" in kinds


def test_no_answer_candidate_without_eligibility():
    # I do not hold the proposition at the claim's floor: the
    # obligation does not fabricate knowledge — clarify instead
    view = _view(beliefs={"anonymous_letter_exists": (0.45, "heard")})
    res = _gen(view, config=_owner_config(), queue=_letter_queue())
    assert "answer" not in _kinds(res)


def test_clarify_generated_in_the_ambiguous_band():
    cfg = load_all()["merchant"]
    from v02.providers.rule_provider import RuleProvider
    provider = RuleProvider(cfg, seed=42)
    view = _view(agent_id="merchant",
                 beliefs={"mayor_embezzled": (0.45, "heard")})
    queue = [{"asker": "soldier", "claim_key": "investigation_probe",
              "proposition": "mayor_embezzled", "tick_asked": 4,
              "addressed_to_me": True, "via": "ask"}]
    res = _gen(view, config=cfg, queue=queue,
               colocated=("soldier",), provider=provider)
    assert "clarify" in _kinds(res)


# ---------------- the withhold veto (design 3.5) ----------------

def test_assert_that_would_light_my_failure_condition_is_vetoed():
    guard = Goal(
        key="never_speak_of_it", desc="x", kind="maintenance",
        failure_condition={
            "type": "event_occurred", "event_type": "speak",
            "actor": "self", "claim": "letter_discussion"})
    view = _view(beliefs={"anonymous_letter_exists": (1.0, "experienced")})
    res = _gen(view, config=_owner_config(), goals=[guard],
               queue=_letter_queue())
    assert "answer" not in _kinds(res)
    assert res["vetoed"] and res["vetoed"][0]["goal"] \
        == "never_speak_of_it"


# ---------------- disclosure duties (design 3.5) ----------------

def _clinic_scenario():
    import json
    return load_scenario(json.loads(CLINIC_WORLD.read_text(
        encoding="utf-8")))


def _clinic_doctor():
    from v02.agent import CharacterAgent
    from v02.providers.rule_provider import RuleProvider
    cfg = load_all(CLINIC_ROOT)["doctor_shen"]
    scenario = _clinic_scenario()
    agent = CharacterAgent(cfg, RuleProvider(cfg, seed=42),
                           scenario=scenario)
    agent.provider.scenario = scenario
    return agent


def test_clinic_doctor_carries_the_duty_and_it_applies():
    from v02.conditions import build_view
    agent = _clinic_doctor()
    assert len(agent.config.disclosure_duties) == 1
    view = build_view(agent, 1)
    items = applicable_duties(view, agent.config, agent.scenario,
                              ["nurse_lin"])
    assert [i["target"] for i in items] == ["nurse_lin"]
    # once I have told her (my own action history says so), the
    # duty stops generating
    agent.state.own_actions.append(
        {"type": "speak", "target": "nurse_lin",
         "claim": "patient_notice", "tick": 1})
    view2 = build_view(agent, 2)
    assert applicable_duties(view2, agent.config, agent.scenario,
                             ["nurse_lin"]) == []


def test_duty_with_unvoiceable_proposition_fails_at_construction():
    import dataclasses

    from v02.agent import CharacterAgent
    from v02.providers.rule_provider import RuleProvider
    cfg = load_all(CLINIC_ROOT)["doctor_shen"]
    bad = dataclasses.replace(cfg, disclosure_duties=[{
        "when": None, "proposition": "no_such_proposition",
        "targets": ["nurse_lin"], "urgency": "normal"}])
    with pytest.raises(ValueError):
        CharacterAgent(bad, RuleProvider(bad, seed=42),
                       scenario=_clinic_scenario())


# ---------------- the queue lifecycle (agent runtime) ----------------

def _perc(tick, events, loc="bar"):
    return {"tick": tick, "time": "21:05", "location": loc,
            "colocated": [{"id": "soldier"}], "events": list(events),
            "muffled": [], "visible_objects": [], "agent_id": "owner",
            "world_public": {"weather": "clear"}}


def _ask_event(etype="ask", target="owner", claim="letter_question"):
    return {"event_id": f"E{tick_counter[0]}", "type": etype,
            "actors": ["soldier", target], "claim": claim,
            "topic": claim, "location": "bar", "text": "..."}


tick_counter = [100]


def _owner_agent():
    from v02.agent import CharacterAgent
    from v02.providers.rule_provider import RuleProvider
    cfg = load_all()["owner"]
    return CharacterAgent(cfg, RuleProvider(cfg, seed=42))


def test_queue_takes_addressed_only_and_expires_with_audit():
    agent = _owner_agent()
    tick_counter[0] += 1
    agent.receive(_perc(1, [_ask_event("ask", "owner")]), None)
    assert len(agent.state.pending_questions) == 1
    # an overheard question (addressed to someone else) is NOT
    # an obligation entry
    tick_counter[0] += 1
    agent.receive(_perc(2, [_ask_event("ask", "merchant")]), None)
    assert len(agent.state.pending_questions) == 1
    # TTL: asked at tick 1; receive at tick 5 -> expired, audited
    agent.receive(_perc(5, []), None)
    assert agent.state.pending_questions == []
    assert agent._expired_questions and \
        agent._expired_questions[0]["asker"] == "soldier"


def test_dequeue_on_deny_and_refuse():
    from types import SimpleNamespace

    from v02.protocol import ActionSpec
    agent = _owner_agent()
    tick_counter[0] += 1
    agent.receive(_perc(1, [_ask_event("ask", "owner")]), None)
    assert len(agent.state.pending_questions) == 1
    decision = SimpleNamespace(action=ActionSpec(
        type="speak", target="soldier", claim="denial"))
    agent._dequeue_answered(decision)
    assert agent.state.pending_questions == []

    tick_counter[0] += 1
    agent.receive(_perc(2, [_ask_event("ask", "owner")]), None)
    assert len(agent.state.pending_questions) == 1
    decision = SimpleNamespace(action=ActionSpec(
        type="refuse", target="soldier"))
    agent._dequeue_answered(decision)
    assert agent.state.pending_questions == []


def test_old_checkpoint_slot_migrates_into_the_queue():
    st = CharacterState.model_validate({
        "character_id": "owner", "location": "bar",
        "personality": {}, "principles": [], "goals": [],
        "pending_question": {"from": "soldier",
                             "claim": "letter_question", "tick": 3,
                             "addressed_to_me": True}})
    assert len(st.pending_questions) == 1
    assert st.pending_questions[0]["asker"] == "soldier"
    assert effective_queue(st)[0]["claim_key"] == "letter_question"


# ---------------- census (design 3.6) ----------------

def test_census_rides_decision_records(tmp_path):
    import asyncio

    from v02.runner import run_simulation
    run_dir = asyncio.run(run_simulation(
        mode="rule", ticks=3, seed=42, run_name="c2_census",
        out_root=tmp_path))
    import json
    import sqlite3
    db = sqlite3.connect(f"file:{run_dir}/acp.sqlite3?mode=ro",
                         uri=True)
    rows = db.execute(
        "select census from agent_decisions where census is not null"
    ).fetchall()
    db.close()
    assert rows, "no census persisted"
    census = json.loads(rows[0][0])
    for key in ("n_generated", "by_source", "by_comm_kind",
                "assert_generated", "answer_generated",
                "search_generated", "chosen_comm_kind", "vetoed"):
        assert key in census
