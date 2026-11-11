"""V0.2-B B2 tests: the Goal Engine.

GoalEvaluator state machine (activation / failure-before-completion
/ progress-from-conditions / paused rule / stateless priority), the
plan-goal goal_id linkage discipline, the review's key example as a
named regression test, and the priority-swap paired-run gate.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from pathlib import Path

import yaml

from v02.agent import CharacterAgent
from v02.config import load_all
from v02.protocol import Goal, PlanUpdate
from v02.providers import make_provider
from v02.providers.rule_provider import RuleProvider
from v02.runner import run_simulation
from v02.scenario import load_scenario

ROOT = Path(__file__).resolve().parent.parent
VARIANT_ROOT = ROOT / "characters_variant"
WAREHOUSE = json.loads(
    (ROOT / "scenarios" / "warehouse_key_world.json").read_text(encoding="utf-8"))


def perc(tick=1, loc="tables", events=()):
    return {"tick": tick, "time": "21:05", "location": loc,
            "colocated": [], "events": list(events), "muffled": [],
            "visible_objects": [], "agent_id": "x",
            "world_public": {"weather": "clear"}}


def make_agent(cid, root=None, scenario=None):
    cfg = load_all(root)[cid] if root else load_all()[cid]
    return CharacterAgent(cfg, RuleProvider(cfg, seed=42), scenario=scenario)


def goals_of(agent):
    return {g.key: g for g in agent.state.goals}


# ---------------- activation ----------------

def test_keeper_goal_activates_at_init_from_private_knowledge():
    agent = make_agent("keeper", root=VARIANT_ROOT,
                       scenario=load_scenario(WAREHOUSE))
    assert goals_of(agent)["find_missing_key"].status == "inactive"
    agent.evaluate_goals(0, cause="init")
    # his private knowledge (key missing, 0.95) meets the >= 0.8
    # activation condition before any tick runs
    assert goals_of(agent)["find_missing_key"].status == "active"
    kinds = [c["kind"] for c in agent._drain_goal_changes()]
    assert "activated" in kinds


def test_traveler_goal_stays_inactive_without_the_knowledge():
    agent = make_agent("traveler", root=VARIANT_ROOT,
                       scenario=load_scenario(WAREHOUSE))
    agent.evaluate_goals(0, cause="init")
    assert goals_of(agent)["learn_key_whereabouts"].status == "inactive"


# ---------------- the review's key example (named regression) ----------------

def test_searched_but_not_found_does_not_complete_the_goal():
    """THE review's key example, as a regression test: the keeper
    searches a searchable place and finds NOTHING. Action success
    (ok=True) is not step fulfilment, is not plan completion, and is
    NOT goal completion — the goal stays active at progress 0 until
    the key is actually in his hands."""
    agent = make_agent("keeper", root=VARIANT_ROOT,
                       scenario=load_scenario(WAREHOUSE))
    agent.evaluate_goals(0, cause="init")
    assert goals_of(agent)["find_missing_key"].status == "active"
    # he searches the door (a searchable place) — successfully
    # executed, objectively empty
    agent.receive(perc(1, "door"), {"ok": True, "found": []})
    goal = goals_of(agent)["find_missing_key"]
    assert goal.status == "active"
    assert goal.progress == 0.0
    # and only the actual find completes it
    agent.receive(perc(2, "back_room"), {"ok": True,
                                         "found": ["warehouse_key"]})
    goal = goals_of(agent)["find_missing_key"]
    assert goal.status == "completed" and goal.progress == 1.0
    kinds = [u.get("change_kind") for u in agent.drain_chain_updates()
             if u.get("kind") == "goal_change"]
    assert "goal_completed" in kinds


# ---------------- evaluator semantics ----------------

def test_failure_is_checked_before_completion():
    agent = make_agent("owner")
    agent.state.goals.append(Goal(
        key="synthetic", desc="both conditions hold", weight=0.5,
        kind="achievement",
        success_condition={"type": "belief_threshold",
                           "belief": "anonymous_letter_exists",
                           "min_confidence": 0.5},
        failure_condition={"type": "belief_threshold",
                           "belief": "mayor_embezzled",
                           "min_confidence": 0.4}))
    agent.evaluate_goals(1, cause="perception")
    # the owner holds BOTH beliefs (letter 1.0, mayor 0.45): failure
    # is evaluated first, so the goal fails rather than completes
    assert goals_of(agent)["synthetic"].status == "failed"


def test_progress_is_condition_satisfaction_not_plan_fraction():
    agent = make_agent("soldier")
    goal = goals_of(agent)["complete_investigation"]
    agent.evaluate_goals(1, cause="perception")
    # letter leaf 0 (no belief), mayor leaf 0 -> progress 0
    assert goal.progress == 0.0
    agent.memory.update_belief("anonymous_letter_exists", 1.0,
                               "experienced", "亲眼读到",
                               source_type="direct_observation",
                               event_id="E1", tick=2)
    agent.evaluate_goals(2, cause="perception")
    # the all() has two leaves: letter satisfied (1.0), the any()
    # mayor/cleared leaf at 0 -> mean progress 0.5, still active
    goal = goals_of(agent)["complete_investigation"]
    assert goal.status == "active" and goal.progress == 0.5


def test_priority_rules_recompute_statelessly():
    agent = make_agent("owner")
    goal = goals_of(agent)["avoid_politics"]
    goal.priority_rules = [{
        "when": {"type": "belief_threshold",
                 "belief": "anonymous_letter_exists",
                 "min_confidence": 0.5},
        "delta": -0.3, "once": False}]
    agent.evaluate_goals(1, cause="perception")
    assert goal.priority_current == 0.6      # 0.9 weight - 0.3
    # the condition stops holding (belief removed in the fixture) ->
    # the priority returns to the static weight: no drift, no latch
    agent.state.beliefs = [b for b in agent.state.beliefs
                           if b.key != "anonymous_letter_exists"]
    agent.evaluate_goals(2, cause="perception")
    assert goal.priority_current == 0.9
    kinds = [c["kind"] for c in agent._drain_goal_changes()]
    assert kinds.count("priority_change") == 2


def test_goal_id_forgery_is_rejected_and_recorded():
    agent = make_agent("owner")
    pm = agent.plan_manager
    pm.apply_update(agent.state, PlanUpdate(
        status="replace", reason="x", new_goal="whatever",
        new_steps=["s1"], goal_id="not_my_goal"), "sig", 1)
    plan = agent.state.current_plan
    assert plan is not None and plan.goal_id is None   # link refused
    kinds = [t["kind"] for t in pm.transitions]
    assert "plan_update_rejected_invalid_goal_id" in kinds


def test_unlinked_plan_with_goal_label_touches_no_goal():
    agent = make_agent("merchant")
    pm = agent.plan_manager
    # a free-label plan whose LABEL equals a goal key (the V0.2-A
    # _sync trap) but with no goal_id: the goal must not move
    pm.set_plan(agent.state, "gain_business", ["pitch"], 1)
    pm.apply_update(agent.state, PlanUpdate(status="advance"), "a1", 2)
    agent.evaluate_goals(3, cause="plan_event")
    goal = goals_of(agent)["gain_business"]
    assert goal.status == "active" and goal.progress == 0.0
    # no change record concerns gain_business (other goals' first
    # progress evaluations are the evaluator's own business)
    assert all(c["goal_key"] != "gain_business"
               for c in agent._drain_goal_changes())


def test_maintenance_goal_gets_no_progress_writes():
    agent = make_agent("owner")
    for tick in (1, 2, 3):
        agent.evaluate_goals(tick, cause="perception")
    goal = goals_of(agent)["protect_tavern"]
    assert goal.status == "active" and goal.progress == 0.0
    assert all(c["goal_key"] != "protect_tavern"
               for c in agent._drain_goal_changes())


# ---------------- the priority-swap paired-run gate ----------------

def _soldier_goal_stats(run_dir: Path) -> dict:
    db = sqlite3.connect(f"file:{run_dir}/acp.sqlite3?mode=ro", uri=True)
    rows = db.execute(
        "select parsed_output from agent_decisions "
        "where character_id='soldier' and error is null "
        "order by tick").fetchall()
    from collections import Counter
    selected = Counter()
    first_search_tick = None
    log = json.loads((run_dir / "world_log.json").read_text(encoding="utf-8"))
    for snap in log:
        for ev in snap["events"]:
            if ev["type"] == "search" and ev["actors"][:1] == ["soldier"]:
                if first_search_tick is None:
                    first_search_tick = snap["tick"]
    for (po,) in rows:
        selected[json.loads(po).get("selected_goal_id")] += 1
    return {"selected": dict(selected), "decisions": len(rows),
            "first_soldier_search_tick": first_search_tick}


def test_priority_swap_flips_the_soldiers_behaviour(tmp_path):
    """B2 gate: swap the weights of the soldier's two achievement
    goals and his behaviour distribution must measurably flip —
    priority is a runtime force, not a display order."""
    base = asyncio.run(run_simulation(mode="rule", ticks=12, seed=42,
                                      run_name="swap_base",
                                      out_root=tmp_path / "base"))
    swapped_root = tmp_path / "chars"
    shutil.copytree(ROOT / "characters_v02", swapped_root)
    goals_path = swapped_root / "soldier" / "goals.yaml"
    data = yaml.safe_load(goals_path.read_text(encoding="utf-8"))
    for g in data["goals"]:
        if g["key"] == "complete_investigation":
            g["weight"] = 0.75
        elif g["key"] == "learn_mayor_truth":
            g["weight"] = 1.0
    goals_path.write_text(yaml.safe_dump(data, allow_unicode=True),
                          encoding="utf-8")
    swapped = asyncio.run(run_simulation(
        mode="rule", ticks=12, seed=42, run_name="swap_swapped",
        out_root=tmp_path / "swapped", characters_root=swapped_root))

    before = _soldier_goal_stats(base)
    after = _soldier_goal_stats(swapped)
    ci_before = before["selected"].get("complete_investigation", 0)
    lmt_after = after["selected"].get("learn_mayor_truth", 0)
    # before: the investigation goal dominates his tagged decisions;
    # after the swap, the mayor-truth goal takes the plurality
    assert ci_before >= before["decisions"] // 2
    assert lmt_after >= after["decisions"] // 2
    assert after["selected"].get("complete_investigation", 0) < ci_before
