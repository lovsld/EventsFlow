"""V0.2-C fix round tests: transmission truthfulness + duty seeking.

The clinic's last two broken rings, at unit level:

  * an utterance that FAILED in the world (its addressee had
    left; the world logged speak_failed) must not satisfy an
    event_occurred "I informed them" condition, and must not
    discharge a disclosure duty — "occurred / told" only counts
    world-confirmed delivery;
  * event_occurred leaves can demand a speech-act stance, so a
    question can never pose as an informing;
  * a disclosure duty whose named recipient is absent and not
    yet truly told generates a duty-driven SEEKING move toward
    the recipient's last-seen location (own perception history
    only), priced by the duty's urgency.

Trajectory-level gates live in the fix-round stage record
(docs/V0.2-C_IMPLEMENTATION_REPORT.md).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from v02.comm_candidates import (applicable_duties, undelivered_duties)
from v02.conditions import (SubjectiveView, evaluate, is_satisfied,
                            validate_subjective)
from v02.config import load_all
from v02.context_builder import build_context
from v02.protocol import Belief, Evidence
from v02.providers.rule_provider import RuleProvider
from v02.scenario import load_scenario

ROOT = Path(__file__).resolve().parent.parent
CLINIC_ROOT = ROOT / "characters_scenario_c"
CLINIC_WORLD = ROOT / "scenarios" / "scenario_c_clinic.json"
CLINIC_SC = load_scenario(
    json.loads(CLINIC_WORLD.read_text(encoding="utf-8")))

BRIEF_LEAF = {"type": "event_occurred", "event_type": "speak",
              "actor": "self", "claim": "patient_notice",
              "stance": "assert"}


def _view(agent_id="doctor_shen", beliefs=None, perceived=(), own=(),
          tick=10, location="triage"):
    return SubjectiveView(
        agent_id=agent_id, tick=tick, location=location,
        beliefs={k: Belief(key=k, confidence=c, kind=kind)
                 for k, (c, kind) in (beliefs or {}).items()},
        perceived_events=list(perceived), own_actions=list(own))


def _speak_rec(ok, tick=9):
    rec = {"tick": tick, "type": "speak", "target": "nurse_lin",
           "claim": "patient_notice", "topic": "patient_notice",
           "stance": "assert"}
    if ok is not None:
        rec["ok"] = ok
    return rec


# ---------------- transmission truthfulness ----------------

def test_failed_own_speech_does_not_satisfy_informing_leaf():
    # the seed42 clinic defect, distilled: the doctor's tick-9
    # speak was rejected by the world (speak_failed) yet its
    # action-log record satisfied the brief condition at tick 10
    view = _view(own=[_speak_rec(ok=False)])
    assert not is_satisfied(BRIEF_LEAF, view)
    # the same record, world-confirmed, satisfies it
    view_ok = _view(own=[_speak_rec(ok=True)])
    assert is_satisfied(BRIEF_LEAF, view_ok)
    # legacy records without an ok field keep the old reading
    view_legacy = _view(own=[_speak_rec(ok=None)])
    assert is_satisfied(BRIEF_LEAF, view_legacy)


def test_stance_demand_rejects_questions_and_untyped_records():
    leaf = {"type": "event_occurred", "event_type": "speak",
            "actor": "any_other", "claim": "patient_notice",
            "stance": "assert"}
    asked = {"tick": 3, "type": "ask", "actor": "porter_wu",
             "target": "nurse_lin", "claim": "patient_inquire",
             "stance": "inquire"}
    # a question about the same proposition is not an informing
    assert not is_satisfied(leaf, _view(perceived=[asked]))
    told = {"tick": 3, "type": "speak", "actor": "porter_wu",
            "target": "nurse_lin", "claim": "patient_notice",
            "stance": "assert"}
    assert is_satisfied(leaf, _view(perceived=[told]))
    # a record that cannot prove its act type does not count
    untyped = {k: v for k, v in told.items() if k != "stance"}
    assert not is_satisfied(leaf, _view(perceived=[untyped]))
    # list form: any of the named stances satisfies
    leaf_any = {**leaf, "stance": ["assert", "deny"]}
    assert is_satisfied(leaf_any, _view(perceived=[told]))


def test_stance_shape_is_validated():
    with pytest.raises(ValueError):
        validate_subjective({**BRIEF_LEAF, "stance": 5})
    with pytest.raises(ValueError):
        validate_subjective({**BRIEF_LEAF, "stance": []})
    validate_subjective(BRIEF_LEAF)            # must not raise
    validate_subjective({**BRIEF_LEAF, "stance": ["assert"]})


# ---------------- duty discharge truthfulness ----------------

def _doctor_view(own):
    return _view(beliefs={"fever_patient_coming": (1.0, "experienced")},
                 own=own)


def test_failed_speak_does_not_discharge_disclosure_duty():
    cfg = load_all(CLINIC_ROOT)["doctor_shen"]
    view = _doctor_view([_speak_rec(ok=False)])
    # target present: the disclose candidate must still be owed
    assert applicable_duties(view, cfg, CLINIC_SC, ["nurse_lin"])
    # target absent: the duty is undelivered (seeking applies)
    assert undelivered_duties(view, cfg, CLINIC_SC, [])
    # a world-confirmed telling discharges it on both sides
    view_ok = _doctor_view([_speak_rec(ok=True)])
    assert not applicable_duties(view_ok, cfg, CLINIC_SC, ["nurse_lin"])
    assert not undelivered_duties(view_ok, cfg, CLINIC_SC, [])
    # nobody home and nothing owed yet: no generation at all
    assert not undelivered_duties(_view(), cfg, CLINIC_SC, [])


# ---------------- duty-driven seeking (provider level) ----------------

def test_absent_duty_recipient_generates_seeking_move():
    from v02.agent import CharacterAgent
    cfg = load_all(CLINIC_ROOT)["nurse_lin"]
    provider = RuleProvider(cfg, seed=42)
    provider.scenario = CLINIC_SC
    agent = CharacterAgent(cfg, provider, scenario=CLINIC_SC)
    st = agent.state
    # the nurse has just found the medicine: she holds the
    # reportable belief (0.95, evidenced by her own search)
    st.beliefs = [b for b in st.beliefs
                  if b.key != "fever_medicine_restocked"]
    st.beliefs.append(Belief(
        key="fever_medicine_restocked", confidence=0.95,
        kind="experienced",
        evidence=[Evidence(source_event_id="search_result:x@22",
                           source_type="direct_observation",
                           reliability=0.95, observed_at=22,
                           source="fever_medicine")]))
    st.holdings.append("fever_medicine")
    # everything else on her plate is settled — only the report
    # remains; the doctor was last seen by her at triage
    for g in st.goals:
        if g.kind == "achievement":
            g.status = "completed"
    st.current_plan = None
    st.perceived_events.append({
        "tick": 21, "type": "speak", "actor": "doctor_shen",
        "target": "porter_wu", "claim": "patient_notice",
        "topic": "patient_notice", "stance": "assert",
        "object": None, "location": "triage", "to": None,
        "found": []})
    ctx = build_context(agent, {"tick": 23, "time": "23:00",
                                "location": "pharmacy", "colocated": [],
                                "events": [], "muffled": [],
                                "visible_objects": []})
    result = asyncio.run(provider.generate_decision(ctx))
    # a disclose-kind candidate was generated (the seeking move),
    # and it won: she heads for the doctor's last-seen location
    assert result.census["by_comm_kind"].get("disclose", 0) >= 1
    assert result.decision.action.type == "move"
    assert result.decision.action.target_location == "triage"


def test_no_seeking_without_duty_data():
    # a cast whose data declares no duties (the tavern owner)
    # generates no seeking candidates: the mechanism is inert
    # without duty data, so duty-free scenarios are untouched
    from v02.agent import CharacterAgent
    cfg = load_all()["owner"]
    provider = RuleProvider(cfg, seed=42)
    agent = CharacterAgent(cfg, provider)
    for g in agent.state.goals:
        if g.kind == "achievement":
            g.status = "completed"
    agent.state.current_plan = None
    ctx = build_context(agent, {"tick": 3, "time": "21:00",
                                "location": "bar", "colocated": [],
                                "events": [], "muffled": [],
                                "visible_objects": []})
    result = asyncio.run(provider.generate_decision(ctx))
    assert result.census["by_comm_kind"].get("disclose", 0) == 0


# ---------------- clinic data uses the contracts ----------------

def test_clinic_data_declares_the_fix_contracts():
    cfgs = load_all(CLINIC_ROOT)
    doctor_goals = {g["key"]: g for g in cfgs["doctor_shen"].goals}
    leaf = doctor_goals["brief_nurse"]["success_condition"]
    assert leaf["stance"] == "assert"
    duties = cfgs["nurse_lin"].disclosure_duties
    assert len(duties) == 1
    assert duties[0]["proposition"] == "fever_medicine_restocked"
    assert duties[0]["targets"] == ["doctor_shen"]
    # every clinic condition (goals + duty whens) validates under
    # the subjective contract, stance field included
    for cfg in cfgs.values():
        for g in cfg.goals:
            for field in ("success_condition", "activation_condition",
                          "failure_condition"):
                validate_subjective(g.get(field), f"{cfg.character_id}.{g['key']}.{field}")
        for d in cfg.disclosure_duties:
            validate_subjective(d.get("when"), "duty.when")
