"""V0.2-C C4 tests: plan granularity + replan loop control (design 5).

Pure level first (semantic fingerprints, context digests, the
structural decisive-falsity test), then the mechanisms at the
PlanManager / agent level: every-receive step fulfilment (5.1),
the narrowed failure rule (5.2), and the replan gate (5.3) in
both its landing points. Trajectory gates live in the C4 stage
record (docs/V0.2-C_IMPLEMENTATION_REPORT.md).
"""
from __future__ import annotations

import json
from pathlib import Path

from v02 import conditions, plan_fingerprint as pf
from v02.agent import CharacterAgent
from v02.config import load_all
from v02.plan_evaluator import decisively_false, evaluate_step
from v02.protocol import PlanStep, PlanUpdate
from v02.providers.rule_provider import RuleProvider
from v02.scenario import load_scenario

ROOT = Path(__file__).resolve().parent.parent
VARIANT_ROOT = ROOT / "characters_variant"
WAREHOUSE = json.loads(
    (ROOT / "scenarios" / "warehouse_key_world.json").read_text(encoding="utf-8"))

SEARCH = {"type": "search", "target": None, "target_location": "door",
          "object": None, "claim": None, "topic": None}


# ---------------- fixtures ----------------

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


def secure_step():
    return PlanStep(
        step_id="secure:warehouse_key", objective="secure warehouse_key",
        action_candidates=[{"type": "search"}, {"type": "show"}],
        success_condition={"type": "object_possession",
                           "object": "warehouse_key", "holder": "self"})


def blocked_keeper():
    """A keeper whose locate step failed twice: plan blocked, and
    (C4) a failure memory recorded."""
    agent = keeper_agent()
    pm = agent.plan_manager
    pm.set_plan(agent.state, "find_missing_key",
                [find_step(), secure_step()], 1, goal_id="find_missing_key")
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


def perc(tick, location, events=(), colocated=()):
    return {"tick": tick, "time": "21:00", "location": location,
            "colocated": list(colocated), "events": list(events),
            "muffled": [], "visible_objects": []}


def tavern_agent(cid="soldier"):
    cfg = load_all()[cid]
    provider = RuleProvider(cfg, seed=42)
    agent = CharacterAgent(cfg, provider)
    agent.evaluate_goals(0, cause="init")
    return agent


# ---------------- fingerprints (design 5.3, pure) ----------------

def test_fingerprint_identical_plans_are_identical():
    steps = [find_step(), secure_step()]
    a = pf.plan_fingerprint(steps, "find_missing_key", "keeper")
    b = pf.plan_fingerprint([find_step(), secure_step()],
                            "find_missing_key", "keeper")
    assert pf.fingerprint_similarity(a, b) == 1.0
    # serializable and stable through a JSON roundtrip
    a2 = json.loads(json.dumps(a))
    assert pf.fingerprint_similarity(a2, b) == 1.0
    # a different goal is never similar
    c = pf.plan_fingerprint(steps, "other_goal", "keeper")
    assert pf.fingerprint_similarity(a, c) == 0.0
    # a subset plan is a partial match (multiset Jaccard 1/2)
    d = pf.plan_fingerprint([find_step()], "find_missing_key", "keeper")
    assert pf.fingerprint_similarity(a, d) == 0.5


def test_fingerprint_normalizes_wording_agents_and_thresholds():
    # threshold jitter INSIDE one 0.25 bucket does not mint a new plan
    s1 = PlanStep(step_id="evidence:x", objective="words one",
                  action_candidates=[{"type": "ask"}],
                  success_condition={"type": "belief_threshold",
                                     "belief": "p", "min_confidence": 0.65})
    s2 = PlanStep(step_id="evidence:y", objective="different words",
                  action_candidates=[{"type": "ask"}],
                  success_condition={"type": "belief_threshold",
                                     "belief": "p", "min_confidence": 0.7})
    a = pf.plan_fingerprint([s1], "g", "soldier")
    b = pf.plan_fingerprint([s2], "g", "soldier")
    assert pf.fingerprint_similarity(a, b) == 1.0
    # a threshold in another bucket does
    s3 = PlanStep(step_id="evidence:z", objective="words",
                  action_candidates=[{"type": "ask"}],
                  success_condition={"type": "belief_threshold",
                                     "belief": "p", "min_confidence": 0.9})
    c = pf.plan_fingerprint([s3], "g", "soldier")
    assert pf.fingerprint_similarity(a, c) < pf.SIMILARITY_THRESHOLD
    # agent references collapse to self/other: a possession by
    # "keeper" and by "traveler" sign identically for a third party
    p1 = PlanStep(step_id="s", success_condition={
        "type": "object_possession", "object": "o", "holder": "keeper"})
    p2 = PlanStep(step_id="s", success_condition={
        "type": "object_possession", "object": "o", "holder": "traveler"})
    assert pf.step_signature(p1, "soldier") == pf.step_signature(p2, "soldier")


def test_context_digest_moves_with_the_world():
    agent = keeper_agent()
    goal = next(g for g in agent.state.goals if g.key == "find_missing_key")
    view = conditions.build_view(agent, 1)
    d0 = pf.context_digest(view, goal)
    # searching a new place changes the digest (the searched set
    # is a digest component — warehouse-style replans survive)
    agent.state.own_actions.append(
        {"tick": 2, "type": "search", "target_location": "cellar",
         "target": None, "object": None, "claim": None, "topic": None,
         "ok": True})
    d1 = pf.context_digest(conditions.build_view(agent, 2), goal)
    assert d1 != d0
    # finding the object changes it too
    agent.state.found_objects.append("warehouse_key")
    d2 = pf.context_digest(conditions.build_view(agent, 3), goal)
    assert d2 != d1


# ---------------- decisive falsity (design 5.2, pure) ----------------

def _view(**kw):
    return conditions.SubjectiveView(agent_id="keeper", tick=1,
                                     location="warehouse", **kw)


def test_decisively_false_only_counts_object_location_leaves():
    v = _view()
    belief = {"type": "belief_threshold", "belief": "goods_ready",
              "min_confidence": 0.9}
    event = {"type": "event_occurred", "event_type": "speak",
             "actor": "any_other", "claim": "goods_ready"}
    obj = {"type": "object_found", "object": "warehouse_key"}
    assert decisively_false(belief, v) is False      # not yet != failed
    assert decisively_false(event, v) is False       # not yet != failed
    assert decisively_false(obj, v) is True          # searched, not there
    # structural over the tree: an `all` falls with any hard branch,
    # an `any` only when every branch is hard-false
    assert decisively_false({"all": [belief, obj]}, v) is True
    assert decisively_false({"any": [belief, obj]}, v) is False
    assert decisively_false({"any": [obj, dict(obj)]}, v) is True
    assert decisively_false({"not": obj}, v) is False


def test_narrowed_failure_rule_spares_asks_and_observes():
    """The clinic doctor's C3 pathology as a unit case: a belief
    step asked about twice no longer blocks (root cause 甲）, and
    an event step observed at twice no longer blocks either."""
    agent = tavern_agent("soldier")
    step = PlanStep(
        step_id="gather:p", objective="gather",
        action_candidates=[{"type": "ask"}, {"type": "observe"}],
        success_condition={"type": "belief_threshold",
                           "belief": "mayor_embezzled",
                           "min_confidence": 0.65})
    view = conditions.build_view(agent, 1)
    ev = evaluate_step(step, action={"type": "ask", "target": "owner"},
                       action_result={"ok": True}, view=view)
    assert ev.fulfilled is False and ev.failed is False
    step2 = PlanStep(
        step_id="advance:0:event_occurred", objective="advance",
        action_candidates=[{"type": "speak"}, {"type": "observe"}],
        success_condition={"type": "event_occurred",
                           "event_type": "speak", "actor": "self",
                           "claim": "patient_notice"})
    ev = evaluate_step(step2, action={"type": "observe"},
                       action_result={"ok": True}, view=view)
    assert ev.fulfilled is False and ev.failed is False


# ---------------- synthesis locality contract (design 5.2) ----------------

def test_synthesized_belief_steps_are_evidence_steps():
    agent = tavern_agent("soldier")
    goal = next(g for g in agent.state.goals
                if g.key == "complete_investigation")
    steps = agent.provider.synthesize_steps(
        goal, conditions.build_view(agent, 1))
    belief_steps = [s for s in steps if s.step_id.startswith("evidence:")]
    assert belief_steps, "no evidence steps synthesized"
    for s in belief_steps:
        cond = s.success_condition
        # the belief leaf never stands alone when the scenario
        # offers a response branch (the tavern does, for both of
        # the soldier's belief leaves)
        assert isinstance(cond, dict) and "any" in cond, s.step_id
        types = {leaf.get("type") for leaf in cond["any"]}
        assert "belief_threshold" in types
        assert types & {"event_occurred", "object_found"}


# ---------------- every-receive fulfilment (design 5.1) ----------------

def test_response_event_closes_the_step_at_its_receive():
    """Ask at tick t, the answer is perceived at tick t+2 with no
    step-consistent action of mine in between: the step completes
    at THAT receive (the single-shot window of B could never
    close this episode)."""
    agent = tavern_agent("soldier")
    step = PlanStep(
        step_id="evidence:letter", objective="hear about the letter",
        action_candidates=[{"type": "ask"}],
        success_condition={"any": [
            {"type": "belief_threshold",
             "belief": "anonymous_letter_exists", "min_confidence": 0.65},
            {"type": "event_occurred", "event_type": "speak",
             "actor": "any_other", "claim": "letter_question"}]})
    agent.plan_manager.set_plan(agent.state, "complete_investigation",
                                [step], 1, goal_id="complete_investigation")
    # a quiet receive: nothing happens, nothing is counted
    agent.receive(perc(2, "door"), None)
    plan = agent.state.current_plan
    assert plan is not None and plan.steps[0].attempts == 0
    # the response arrives as a perceived event; my pending action
    # was an unrelated wait — the step still closes now
    agent.receive(perc(3, "door", events=[
        {"event_id": "E9", "tick": 3, "type": "speak",
         "actors": ["merchant"], "location": "door",
         "claim": "letter_question", "topic": "letter_question",
         "text": "…"}]), {"ok": True})
    assert agent.state.current_plan is None      # single step: completed
    kinds = [(u["transition_kind"], u.get("reason") or "")
             for u in agent.drain_chain_updates()
             if u["kind"] == "plan_transition"]
    assert any(k == "step_evaluated" and "every-receive" in r
               for k, r in kinds)
    assert any(k == "plan_completed" for k, r in kinds)


def test_waiting_receives_never_count_failures():
    agent = tavern_agent("soldier")
    step = PlanStep(
        step_id="evidence:letter", objective="hear about the letter",
        action_candidates=[{"type": "ask"}],
        success_condition={"type": "belief_threshold",
                           "belief": "anonymous_letter_exists",
                           "min_confidence": 0.65})
    agent.plan_manager.set_plan(agent.state, "complete_investigation",
                                [step], 1, goal_id="complete_investigation")
    for tick in (2, 3, 4):
        agent.receive(perc(tick, "door"), {"ok": True})
    plan = agent.state.current_plan
    assert plan is not None and plan.status == "active"
    assert plan.steps[0].attempts == 0


# ---------------- failure memory + the gate (design 5.3) ----------------

def test_failure_memory_records_blocked_plans():
    agent, plan = blocked_keeper()
    mem = agent.state.plan_failure_memory
    assert len(mem) == 1
    entry = mem[0]
    assert entry["cause"] == "blocked"
    assert entry["goal_id"] == "find_missing_key"
    assert entry["failed_step_id"] == "locate:warehouse_key"
    assert entry["failed_step_condition"] == {
        "type": "object_found", "object": "warehouse_key"}


def test_replan_gate_converts_same_plan_same_world_to_suspend():
    agent, plan = blocked_keeper()
    pm = agent.plan_manager
    note = pm.execute_recovery(agent.state, 5, "replan", "fresh eyes",
                               source="provider",
                               new_steps=[find_step(), secure_step()])
    assert note == "replan_gated"
    assert plan.status == "suspended"
    assert plan.reactivate_when == {"type": "object_found",
                                    "object": "warehouse_key"}
    kinds = [t["kind"] for t in pm.transitions]
    assert "replan_gated" in kinds
    rec = [t for t in pm.transitions if t["kind"] == "recovery"]
    assert rec and "source=gate" in (rec[-1]["reason"] or "")


def test_replan_gate_lets_a_changed_world_through():
    agent, plan = blocked_keeper()
    # the world moved: the keeper has since searched a new place
    agent.state.own_actions.append(
        {"tick": 4, "type": "search", "target_location": "cellar",
         "target": None, "object": None, "claim": None, "topic": None,
         "ok": True})
    pm = agent.plan_manager
    note = pm.execute_recovery(agent.state, 5, "replan", "fresh eyes",
                               source="provider",
                               new_steps=[find_step(), secure_step()])
    assert note == "recovered_replan"
    assert agent.state.current_plan is not None
    assert agent.state.current_plan.plan_id != plan.plan_id


def test_synthesis_entry_is_gated_too():
    agent, plan = blocked_keeper()
    # the blocked plan is abandoned through the recovery machine,
    # leaving only the failure memory behind
    pm = agent.plan_manager
    assert pm.execute_recovery(agent.state, 5, "abandon", "hopeless",
                               source="provider") == "recovered_abandon"
    assert agent.state.current_plan is None
    # the provider re-synthesizes the identical plan in the same
    # world: refused at the synthesis entry, nothing is set
    note = pm.apply_update(agent.state, PlanUpdate(
        status="replace", reason="synthesized plan for goal find_missing_key",
        new_goal="find_missing_key",
        new_steps=[find_step(), secure_step()],
        goal_id="find_missing_key"), "wait::", 6)
    assert note == "replan_gated"
    assert agent.state.current_plan is None
    kinds = [t["kind"] for t in pm.transitions]
    assert "replan_gated" in kinds


def test_gate_soft_cap_pauses_synthesis_until_the_world_moves():
    agent, plan = blocked_keeper()
    pm = agent.plan_manager
    assert pm.execute_recovery(agent.state, 5, "abandon", "hopeless",
                               source="provider") == "recovered_abandon"
    # three gated re-syntheses at an unchanged digest hit the cap
    for tick in (6, 7, 8):
        note = pm.apply_update(agent.state, PlanUpdate(
            status="replace", reason="synthesized",
            new_goal="find_missing_key",
            new_steps=[find_step(), secure_step()],
            goal_id="find_missing_key"), "wait::", tick)
        assert note == "replan_gated"
    # at the cap, even a DIFFERENT plan for the goal is refused
    other = PlanStep(step_id="go:cellar", objective="go",
                     action_candidates=[{"type": "move"}],
                     success_condition={"type": "agent_at_location",
                                        "agent": "self",
                                        "location": "cellar"})
    note = pm.apply_update(agent.state, PlanUpdate(
        status="replace", reason="synthesized",
        new_goal="find_missing_key", new_steps=[other],
        goal_id="find_missing_key"), "wait::", 9)
    assert note == "replan_gated"
    assert agent.state.current_plan is None
    # the world moves (a place searched): synthesis resumes
    agent.state.own_actions.append(
        {"tick": 9, "type": "search", "target_location": "cellar",
         "target": None, "object": None, "claim": None, "topic": None,
         "ok": True})
    note = pm.apply_update(agent.state, PlanUpdate(
        status="replace", reason="synthesized",
        new_goal="find_missing_key", new_steps=[other],
        goal_id="find_missing_key"), "wait::", 10)
    assert note == "replaced"
    assert agent.state.current_plan is not None
