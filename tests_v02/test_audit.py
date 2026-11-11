"""Step-1 audit tests for ACP V0.2 (pytest).

Covered: the M1 consistency definition against hand-picked cases;
every metric (M1-M6) against a small SYNTHETIC run whose expected
values are computed by hand in the comments; and the audit's
structural invariants on a fresh short rule simulation (prediction
bases fully truthful, zero committed-legality violations, zero
knowledge leaks).

V0.2-B B3 (pre-registered disposition #5): the M1 consistency
definition is now the design's TEMPLATE definition — an action is
consistent with a step iff it matches one of the step's declared
action_candidates templates field-for-field (v02/plan_evaluator.py,
shared with the runtime's StepEvaluator). The hand-picked cases
below re-express the old label-predicate cases equivalently
against that definition; the synthetic run's plan fixture carries
structured steps and its hand-computed expectations are unchanged.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from v02.audit import audit_run
from v02.persistence import Store
from v02.plan_evaluator import action_matches_templates
from v02.runner import run_simulation

ROOT = Path(__file__).resolve().parent.parent


# ---------- M1 template consistency: hand-picked cases ----------

def _act(type_, target=None, loc=None, claim=None, topic=None, obj=None):
    return {"type": type_, "target": target, "target_location": loc,
            "claim": claim, "topic": topic, "object": obj}


def test_step_predicates_hand_cases():
    # a "return to the front" step, expressed as data: move templates
    # naming the front locations — a move to the tables matches, a
    # speech action does not (the old table's shortest-path subtlety
    # is now the synthesizer's job when it AUTHORS the templates).
    front = [{"type": "move", "target_location": "tables"},
             {"type": "move", "target_location": "bar"}]
    assert action_matches_templates(_act("move", loc="tables"), front)
    assert not action_matches_templates(_act("speak", target="owner"), front)
    # a "confront the owner" step: verb + target pinned.
    confront_owner = [{"type": "confront", "target": "owner"},
                      {"type": "ask", "target": "owner"}]
    assert action_matches_templates(
        _act("confront", target="owner"), confront_owner)
    assert not action_matches_templates(
        _act("speak", target="owner"), confront_owner)
    # a "press for evidence" step: pressure verbs, search, approach.
    press = [{"type": "ask", "target": "owner"},
             {"type": "confront", "target": "owner"},
             {"type": "search"},
             {"type": "move", "target_location": "back_room"}]
    assert action_matches_templates(_act("search", loc="back_room"), press)
    assert not action_matches_templates(
        _act("speak", target="owner", claim="greeting"), press)
    # field-subset semantics: a template pinning only the claim slot
    # matches any verb carrying that claim (topic aliases claim).
    denial = [{"claim": "denial"}]
    assert action_matches_templates(_act("speak", claim="denial"), denial)
    assert action_matches_templates(_act("speak", topic="denial"), denial)
    assert not action_matches_templates(_act("work"), denial)
    # a step with NO templates (a legacy free-text step) matches
    # nothing — it is unmeasurable under this definition, and the
    # audit excludes it from the denominators rather than failing it.
    assert not action_matches_templates(_act("wait"), [])


# ---------- synthetic run: every metric hand-computed ----------

def _ctx(location, colocated, beliefs, trust, plan_step=None, plan=None,
         available=()):
    return {"character_id": "x", "tick": 0, "location": location,
            "perception": {"colocated": [{"id": c} for c in colocated],
                           "events": []},
            "beliefs": beliefs, "trust": trust, "plan_step": plan_step,
            "current_plan": plan, "available_actions": list(available)}


def _decision(action, plan_status="continue"):
    return {"action": action,
            "plan_update": {"status": plan_status, "reason": None,
                            "new_goal": None, "new_steps": None}}


def _build_synthetic_run(run_dir: Path) -> Path:
    run_dir.mkdir(parents=True)
    store = Store("audit_synth", sqlite_path=str(run_dir / "acp.sqlite3"))
    press_templates = [{"type": "ask", "target": "owner"},
                       {"type": "confront", "target": "owner"},
                       {"type": "search"}]
    plan = {"plan_id": "plan_001", "goal": "press_for_evidence",
            "steps": [{"step_id": "press", "objective": "press_for_evidence",
                       "action_candidates": press_templates,
                       "success_condition": None}],
            "current_step": 0, "commitment": 0.8, "status": "active"}
    plan_step = {"plan_id": "plan_001", "goal": "press_for_evidence",
                 "step_index": 0, "step": "press_for_evidence",
                 "step_id": "press",
                 "action_candidates": press_templates,
                 "steps_total": 1, "commitment": 0.8, "status": "active"}
    mayor = [{"key": "mayor_embezzled", "confidence": 0.6, "kind": "heard",
              "desc": "rumor"}]
    trust = {"owner": 0.3, "merchant": 0.3}

    def decide(tick, who, ctx, action, exec_result):
        store.record_decision({
            "tick": tick, "character_id": who,
            "decision_key": f"{who}:{tick}", "context": ctx,
            "provider": "rule", "model": "rule-v0.1-port",
            "prompt_version": "acp-v0.2.0", "context_summary": "",
            "input_tokens": 0, "output_tokens": 0, "raw_output": "",
            "parsed_output": _decision(action),
            "reasoning_summary": "", "execution_result": exec_result,
            "error": None})

    def predict(tick, who, action_type, target, basis, refusal_expected=None):
        store.record_prediction({
            "prediction_id": f"pred_{who}_{tick}", "character_id": who,
            "tick": tick, "action_type": action_type, "target": target,
            "refusal_expected": refusal_expected, "basis": basis},
            f"{who}:{tick}")

    # D1 soldier t0: plain move, no plan. Basis: empty perception.
    decide(0, "soldier",
           _ctx("door", [], [], trust, available=[
               {"type": "move", "target_location": "tables"},
               {"type": "observe"}]),
           _act("move", loc="tables"), {"ok": True})
    predict(0, "soldier", "move", None,
            {"location": "door", "colocated": [], "trust_in_target": None,
             "perceived_ids": []})
    # D2 soldier t1: confront owner with mayor_hint, belief 0.6 >= 0.55
    # -> M1a consistent, M2 supported. Its prediction is unexpectedly
    # refused (evaluated at t2) -> the M3 case.
    decide(1, "soldier",
           _ctx("tables", ["owner"], mayor, trust, plan_step=plan_step,
                plan=plan, available=[{"type": "confront", "target": "owner"},
                                      {"type": "observe"}]),
           _act("confront", target="owner", claim="mayor_hint",
                topic="mayor_hint"),
           {"ok": True})
    predict(1, "soldier", "confront", "owner",
            {"location": "tables", "colocated": ["owner"],
             "trust_in_target": 0.3, "perceived_ids": ["E0001"]},
            refusal_expected=False)
    store.update_prediction_error("pred_soldier_1", {
        "mismatches": ["refusal"], "surprise": 0.2,
        "details": {"refusal": {"predicted": False, "actual": True}}}, 2)
    # D3 soldier t2: greeting under a press_for_evidence plan -> M1a
    # inconsistent; not an M3 repeat (different signature).
    decide(2, "soldier",
           _ctx("tables", ["owner"], mayor, trust, plan_step=plan_step,
                plan=plan, available=[{"type": "speak", "target": "owner"},
                                      {"type": "observe"}]),
           _act("speak", target="owner", claim="greeting", topic="greeting"),
           {"ok": True})
    predict(2, "soldier", "speak", "owner",
            {"location": "tables", "colocated": ["owner"],
             "trust_in_target": 0.3, "perceived_ids": ["E0001"]})
    # D4 soldier t3: confront mayor_hint with NO supporting belief ->
    # M2 violation; same signature as the refused D2 -> M3 exact
    # repeat, this time not refused again.
    decide(3, "soldier",
           _ctx("tables", ["owner"], [], trust, available=[
               {"type": "confront", "target": "owner"}]),
           _act("confront", target="owner", claim="mayor_hint",
                topic="mayor_hint"),
           {"ok": True})
    predict(3, "soldier", "confront", "owner",
            {"location": "tables", "colocated": ["owner"],
             "trust_in_target": 0.3, "perceived_ids": ["E0001"]},
            refusal_expected=False)
    store.update_prediction_error("pred_soldier_3", {
        "mismatches": [], "surprise": 0.0,
        "details": {"refusal": {"predicted": False, "actual": False}}}, 4)
    # D5 owner t1: a speak the Resolver rejects (target_left) -> M4.
    decide(1, "owner",
           _ctx("tables", ["soldier"], [], {"soldier": 0.3, "merchant": 0.3},
                available=[{"type": "speak", "target": "soldier"}]),
           _act("speak", target="soldier", claim="denial", topic="denial"),
           {"ok": False, "reason": "target_left"})
    predict(1, "owner", "speak", "soldier",
            {"location": "tables", "colocated": ["soldier"],
             "trust_in_target": 0.3, "perceived_ids": []})

    # one delivery: soldier perceives the owner's move at tick 1
    store.record_deliveries([{"tick": 1, "agent_id": "soldier",
                              "delivered_event_ids": ["E0001"],
                              "muffled_event_ids": []}])
    events = [
        {"event_id": "E0001", "tick": 1, "type": "move", "actors": ["owner"],
         "location": "tables", "from": "bar", "to": "tables"},
        {"event_id": "E0002", "tick": 1, "type": "move", "actors": ["soldier"],
         "location": "tables", "from": "door", "to": "tables"},
        {"event_id": "E0003", "tick": 1, "type": "speak_failed",
         "actors": ["owner"], "location": "tables", "target": "soldier",
         "reason": "target_left"},
    ]
    store.record_events(events)
    # M5: one unjustified abandon + one justified replace -> rate 1/2
    store.record_plan_transition({
        "tick": 2, "character_id": "soldier", "decision_key": "soldier:2",
        "plan_id": "plan_001", "kind": "abandon", "reason": "",
        "before": None, "after": None})
    store.record_plan_transition({
        "tick": 3, "character_id": "soldier", "decision_key": "soldier:3",
        "plan_id": "plan_001", "kind": "replace",
        "reason": "heard the letter discussed", "before": None, "after": None})

    (run_dir / "world_log.json").write_text(json.dumps([{
        "tick": 1, "time": "21:05",
        "positions": {"owner": "tables", "merchant": "tables",
                      "soldier": "tables"},
        "events": events, "deliveries": []}]), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps({
        "run_id": "audit_synth", "seed": 99, "mode": "rule", "ticks": 4,
        "total_events": 3}), encoding="utf-8")
    return run_dir


def test_audit_synthetic_run_hand_computed(tmp_path):
    rep = audit_run(_build_synthetic_run(tmp_path / "synth"))
    # M1: 2 planned decisions (D2 consistent, D3 not) -> 1/2 both ways
    m1 = rep["m1_plan_alignment"]
    assert m1["decisions"] == 5 and m1["with_active_plan"] == 2
    assert m1["judgeable"] == 2
    assert m1["m1a_current_step"] == {"consistent": 1, "judged": 2, "rate": 0.5}
    assert m1["m1b_any_plan_step"]["rate"] == 0.5
    # M2: D2 supported, D4 a violation -> 1/2, exactly one violation
    m2 = rep["m2_knowledge_grounding"]
    assert m2["asserting_actions"] == 2 and m2["supported"] == 1
    assert m2["rate"] == 0.5
    assert len(m2["violations"]) == 1
    assert m2["violations"][0]["tick"] == 3
    assert m2["reverse_check"]["violations"] == []
    # M3: one unexpected-refusal case; D4 is an exact repeat that was
    # not refused again; expectation never flipped to True beforehand
    m3 = rep["m3_refusal_adaptation"]
    assert m3["case_count"] == 1
    (case,) = m3["unexpected_refusal_cases"]
    assert case["refused_signature"] == "confront:owner:mayor_hint"
    assert case["repeat_count"] == 1
    assert case["exact_repeats"][0]["tick"] == 3
    assert case["exact_repeats"][0]["refused_again"] is False
    assert m3["repeats_refused_again"] == 0
    # M4: one rejection of five submissions; committed log is legal;
    # every action was in its context's available_actions
    m4 = rep["m4_legality"]
    assert m4["submissions"] == 5 and m4["rejected"] == 1
    assert m4["rejection_rate"] == 0.2
    assert m4["rejection_reasons"] == {"target_left": 1}
    assert m4["committed_legality"]["violations"] == []
    assert m4["available_action_check"]["rate"] == 1.0
    # M5: 1 of 2 changes justified; contexts show plan_001 with two
    # plan-id switches (None->plan at t1, plan->None at t3)
    m5 = rep["m5_plan_changes"]
    assert m5["abandon_or_replace_rows"] == 2 and m5["with_reason"] == 1
    assert m5["rate"] == 0.5
    assert m5["reconstruction"]["distinct_plan_ids_in_contexts"] == 1
    assert m5["reconstruction"]["plan_id_switches_between_decisions"] == 2
    # M6: all five predictions' bases match context + reconstruction
    m6 = rep["m6_basis_truth"]
    assert m6["predictions_checked"] == 5
    assert m6["per_field"]["all_fields"]["rate"] == 1.0


def test_audit_synthetic_m6_detects_lies(tmp_path):
    run_dir = _build_synthetic_run(tmp_path / "synth2")
    # tamper: soldier's tick-1 basis claims an event he never perceived
    store = Store("audit_synth", sqlite_path=str(run_dir / "acp.sqlite3"))
    from v02.persistence import PredictionRow
    with store.Session() as s:
        row = s.query(PredictionRow).filter_by(
            prediction_id="pred_soldier_1").one()
        pj = json.loads(row.prediction_json)
        pj["basis"]["perceived_ids"] = ["E0001", "E9999"]
        row.prediction_json = json.dumps(pj)
        s.commit()
    rep = audit_run(run_dir)
    m6 = rep["m6_basis_truth"]
    assert m6["per_field"]["perceived_ids"]["match"] == 4
    assert m6["per_field"]["all_fields"]["rate"] == 0.8
    assert {"agent": "soldier", "tick": 1, "field": "perceived_ids"} \
        in m6["mismatch_examples"]


# ---------- fresh simulation: structural invariants ----------

def test_audit_fresh_rule_run_invariants(tmp_path):
    run_dir = asyncio.run(run_simulation(
        mode="rule", ticks=6, seed=42, run_name="audit_fresh",
        out_root=tmp_path))
    rep = audit_run(run_dir)
    assert rep["decisions"] == 18
    assert rep["m6_basis_truth"]["per_field"]["all_fields"]["rate"] == 1.0
    assert rep["m4_legality"]["committed_legality"]["violations"] == []
    assert rep["m4_legality"]["available_action_check"]["rate"] == 1.0
    assert rep["m2_knowledge_grounding"]["violations"] == []
    assert rep["m2_knowledge_grounding"]["reverse_check"]["violations"] == []
    # M5 (V0.2-B adjudication, parked item P-6): the old pin asserted
    # rate == 1.0, which presupposed that this short run CONTAINS
    # abandon/replace rows (it did under the old architecture, where
    # plan churn was largely an artefact of the interpretation layer
    # setting and replacing plans). Under the current architecture
    # this fresh 6-tick run produces NO abandon/replace rows at all,
    # so the honest M5 value is "unmeasurable": zero denominator
    # rows and a None rate. Asserting a numeric rate here would
    # fabricate a sample; the measurable-case semantics of M5 are
    # covered by the synthetic run above (rate 0.5, hand-computed).
    assert rep["m5_plan_changes"]["abandon_or_replace_rows"] == 0
    assert rep["m5_plan_changes"]["rate"] is None
