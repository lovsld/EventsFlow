"""V0.2-B B1 tests: Scenario Capability.

Scenario schema + load-time validation, the Capability Registry,
scenario threading (the functional replacement for the deleted
adjacency-identity test — pre-registered disposition #7), the three
generic interpretation channels replacing _interpret, generic
found-object effects, the disclosure gate, and the Generic Candidate
Generator with its plan synthesizer.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from v02 import broker, context_builder, world
from v02.agent import CharacterAgent
from v02.config import load_all
from v02.providers import make_provider
from v02.providers.rule_provider import RuleProvider
from v02.runner import run_simulation
from v02.scenario import CapabilityRegistry, load_scenario
from v02.world_config import DEFAULT_WORLD

ROOT = Path(__file__).resolve().parent.parent
VARIANT_ROOT = ROOT / "characters_variant"
WAREHOUSE = json.loads(
    (ROOT / "scenarios" / "warehouse_key_world.json").read_text(encoding="utf-8"))


def perc(tick: int, loc: str, colocated=(), events=()) -> dict:
    return {"tick": tick, "time": "21:00", "location": loc,
            "colocated": [{"id": c, "name": c, "role": "x"} for c in colocated],
            "events": list(events), "muffled": [], "visible_objects": [],
            "agent_id": "x", "world_public": {"weather": "clear"}}


def speech(tick: int, eid: str, actor: str, target: str, claim: str,
           verb: str = "speak") -> dict:
    return {"event_id": eid, "tick": tick, "type": verb,
            "actors": [actor, target], "location": "tables",
            "claim": claim, "topic": claim, "text": "…"}


def make_agent(cid: str, scenario=None, root=None) -> CharacterAgent:
    cfg = load_all(root)[cid] if root else load_all()[cid]
    return CharacterAgent(cfg, make_provider("mock", cfg, seed=42),
                          scenario=scenario)


# ---------------- schema & validation ----------------

def test_scenario_validation_names_the_culprit():
    bad = json.loads(json.dumps(DEFAULT_WORLD))
    bad["adjacency"]["bar"] = ["nowhere"]
    with pytest.raises(ValueError, match="nowhere"):
        load_scenario(bad)
    bad2 = json.loads(json.dumps(DEFAULT_WORLD))
    bad2["objects"]["anonymous_letter"]["location"] = "attic"
    with pytest.raises(ValueError, match="attic"):
        load_scenario(bad2)


def test_tavern_scenario_file_mirrors_default_world():
    mirror = json.loads(
        (ROOT / "scenarios" / "tavern_letter.json").read_text(encoding="utf-8"))
    mirror.pop("comment", None)
    assert mirror == json.loads(json.dumps(DEFAULT_WORLD))


def test_no_module_level_adjacency_constants_remain():
    # the threading contract (design 3.7): no consumer keeps a
    # default-graph constant any more
    assert not hasattr(context_builder, "ADJACENCY")
    assert not hasattr(broker, "ADJACENCY")
    assert not hasattr(world, "ADJACENCY")
    from v02.providers import rule_provider
    assert not hasattr(rule_provider, "ADJACENCY")


# ---------------- capability registry ----------------

def test_registry_rules_on_the_tavern():
    reg = CapabilityRegistry(load_scenario(None))
    at_back = reg.available_actions(location="back_room", colocated=[],
                                    held_objects=[])
    assert any(a["type"] == "search" for a in at_back)
    at_bar = reg.available_actions(location="bar", colocated=["merchant"],
                                   held_objects=[])
    assert not any(a["type"] == "search" for a in at_bar)
    assert {a["target_location"] for a in at_bar if a["type"] == "move"} \
        == {"tables"}
    # show requires a DISPLAYABLE held object and a colocated other
    no_show = reg.available_actions(location="bar", colocated=["merchant"],
                                    held_objects=[])
    assert not any(a["type"] == "show" for a in no_show)
    with_letter = reg.available_actions(
        location="bar", colocated=["merchant"],
        held_objects=["anonymous_letter"])
    assert any(a["type"] == "show" and a["object"] == "anonymous_letter"
               for a in with_letter)


# ---------------- threading (disposition #7 replacement) ----------------

CUSTOM_WORLD = {
    "scenario_id": "custom_graph",
    "locations": ["bar", "tables", "door", "back_room", "outside"],
    # a DIFFERENT graph: bar joins the door directly; the back room
    # is reachable only through the door
    "adjacency": {"bar": ["tables", "door"], "tables": ["bar"],
                  "door": ["bar", "back_room", "outside"],
                  "back_room": ["door"], "outside": ["door"]},
    # searchable scenery at the DOOR only (the tavern has it in the
    # back room) — capability follows the data, not the place's name
    "scenery": {"door": [{"id": "hook", "searchable": True}]},
    "objects": {}, "claims": {}, "speech_lines": {}, "ambient": [],
}


def test_nondefault_graph_threads_everywhere():
    from v02.world import WorldEngine, resolve
    from v02.interaction import InteractionManager
    import random
    scenario = load_scenario(CUSTOM_WORLD)
    configs = load_all()

    # (a) the World Engine's resolver honours the custom graph
    engine = WorldEngine(configs, seed=42,
                         world_config=scenario.to_world_config())
    engine.state["agents"]["owner"]["location"] = "bar"
    engine.step({"owner": {"verb": "move", "target_location": "door"}})
    # bar->door exists in the custom graph (not in the tavern graph)
    assert engine.state["agents"]["owner"]["location"] == "door"
    engine.state["agents"]["owner"]["location"] = "bar"
    engine.step({"owner": {"verb": "move", "target_location": "back_room"}})
    # bar->back_room does NOT exist here (it does in the tavern)
    assert engine.state["agents"]["owner"]["location"] == "bar"

    # (b) the Broker muffles speech across CUSTOM adjacency (bar<->door
    # are neighbours here; they are not in the tavern graph)
    br = broker.EventBroker(["owner", "soldier"], scenario=scenario)
    snap = {"tick": 1, "time": "21:00", "weather": "rain",
            "agents": {"owner": {"location": "bar", "name": "o", "role": "r"},
                       "soldier": {"location": "door", "name": "s",
                                   "role": "r"}},
            "locations": {loc: {"objects": []} for loc in CUSTOM_WORLD["locations"]}}
    ev = {"event_id": "E1", "tick": 1, "type": "speak",
          "actors": ["soldier", "owner"], "location": "door"}
    records = br.publish(snap, [ev])
    owner_rec = next(r for r in records if r["agent_id"] == "owner")
    assert "E1" in owner_rec["muffled_event_ids"]

    # (c) the Context Builder offers search at the door (searchable
    # scenery there) and NOT in the back room, from the same instance
    agent = make_agent("keeper", scenario=load_scenario(WAREHOUSE),
                       root=VARIANT_ROOT)
    custom_agent = CharacterAgent(
        load_all()["owner"], make_provider("mock", load_all()["owner"],
                                           seed=42),
        scenario=scenario)
    ctx = context_builder.build_context(custom_agent, perc(1, "door"))
    assert any(a["type"] == "search" for a in ctx.available_actions)
    ctx2 = context_builder.build_context(custom_agent, perc(1, "back_room"))
    assert not any(a["type"] == "search" for a in ctx2.available_actions)
    assert agent is not None  # (variant agent constructs with its scenario)


# ---------------- the three interpretation channels ----------------

def test_channel1_testimony_is_trust_discounted():
    agent = make_agent("soldier")
    agent.receive(perc(1, "tables", events=[
        speech(1, "E1", "merchant", "soldier", "mayor_late_payment")]), None)
    b = agent.memory.belief("mayor_embezzled")
    assert b is not None and b.kind == "heard"
    # declared base 0.5 x (0.4 + 0.6 x trust 0.3) = 0.29
    assert b.confidence == pytest.approx(0.29)


def test_undeclared_claim_has_no_belief_effect():
    agent = make_agent("soldier")
    before = {b.key for b in agent.state.beliefs}
    agent.receive(perc(1, "tables", events=[
        speech(1, "E1", "merchant", "soldier", "some_undeclared_rumor")]), None)
    assert {b.key for b in agent.state.beliefs} == before


def test_channel2_sensitive_questions_decay():
    agent = make_agent("owner")
    ev1 = speech(1, "E1", "soldier", "owner", "investigation_probe", "ask")
    ev2 = speech(2, "E2", "soldier", "owner", "investigation_probe", "ask")
    a0 = agent.state.emotions["arousal"]
    agent.receive(perc(1, "bar", events=[ev1]), None)
    a1 = agent.state.emotions["arousal"]
    agent.receive(perc(2, "bar", events=[ev2]), None)
    a2 = agent.state.emotions["arousal"]
    assert a1 - a0 == pytest.approx(0.25 * 0.9)        # caution .9, n=1
    assert a2 - a1 == pytest.approx(0.25 * 0.9 / 2)    # decayed, n=2


def test_channel3_denial_spawns_conceals_belief():
    agent = make_agent("soldier")
    agent.receive(perc(1, "back_room"), {"found": ["anonymous_letter"]})
    assert agent.memory.belief("anonymous_letter_exists").confidence == 1.0
    asked = speech(2, "E2", "merchant", "owner", "letter_question", "ask")
    denial = speech(2, "E3", "owner", "merchant", "denial")
    agent.receive(perc(2, "tables", events=[asked, denial]), None)
    b = agent.memory.belief("conceals:owner")
    assert b is not None and b.kind == "inferred"
    assert 0.5 <= b.confidence <= 0.8


def test_interpretation_creates_no_plans():
    agent = make_agent("merchant")
    agent.receive(perc(1, "tables", events=[
        speech(1, "E1", "soldier", "merchant", "letter_discussion")]), None)
    assert agent.state.current_plan is None
    pts = [u for u in agent.drain_chain_updates()
           if u.get("kind") == "plan_transition"]
    assert pts == []


# ---------------- generic found-object effects ----------------

def test_found_effects_come_from_the_declaration():
    agent = make_agent("soldier")
    agent.receive(perc(1, "back_room"), {"found": ["anonymous_letter"]})
    letter = agent.memory.belief("anonymous_letter_exists")
    assert letter.confidence == 1.0 and letter.kind == "experienced"
    mayor = agent.memory.belief("mayor_embezzled")
    assert mayor is not None and mayor.confidence == pytest.approx(0.6)
    assert "anonymous_letter" in agent.state.found_objects
    # on_found=inspect: the letter does NOT change hands subjectively
    assert "anonymous_letter" not in agent.state.holdings


def test_found_acquire_object_enters_holdings():
    scenario = load_scenario(WAREHOUSE)
    agent = make_agent("keeper", scenario=scenario, root=VARIANT_ROOT)
    agent.receive(perc(1, "back_room"), {"found": ["warehouse_key"]})
    assert "warehouse_key" in agent.state.holdings
    assert "warehouse_key" in agent.state.found_objects


# ---------------- disclosure gate ----------------

def _owner_facing_question(trust: float):
    cfg = load_all()["owner"]
    provider = RuleProvider(cfg, seed=42)
    agent = CharacterAgent(cfg, provider)
    agent.state.location = "tables"
    if trust != 0.3:
        agent.memory.adjust_trust("soldier", trust - 0.3)
    # the owner's own letter-origin inquiry is repetition-saturated
    # (in real runs he asks it constantly), so this fixture isolates
    # the disclosure gate's margin: answer vs deny for a question
    # addressed to him about the letter
    agent.state.action_counts["ask:soldier:letter_origin_inquiry"] = 6
    agent.state.action_counts["ask:merchant:letter_origin_inquiry"] = 6
    agent.state.pending_question = {"from": "soldier",
                                    "claim": "letter_question",
                                    "topic": "letter_question", "tick": 1,
                                    "addressed_to_me": True}
    return agent, provider


def _response_pair_scores(trust: float) -> dict:
    """The disclosure gate's margin, measured on the two response
    candidates themselves (answer-from-knowledge vs denial): the
    gate is the claim that their order flips with trust in the
    asker relative to the owner's disclosure threshold (0.75)."""
    agent, provider = _owner_facing_question(trust)
    ctx = context_builder.build_context(
        agent, perc(2, "tables", colocated=["soldier"]))
    view = provider._view(ctx)
    cands: list = []
    provider._social_candidates(cands, ctx, view, {})
    out = {}
    for c in cands:
        claim = c["action"].get("claim")
        if c["action"]["type"] == "speak" and claim:
            out[claim] = provider._score(
                c, ctx, view, None, None, [], None, {})["total"]
    return out


def test_disclosure_gate_denies_the_untrusted():
    scores = _response_pair_scores(0.3)   # threshold is 0.75
    # below the threshold, denying beats answering with what he knows
    assert scores["denial"] > scores["letter_discussion"]


def test_disclosure_gate_answers_the_trusted():
    scores = _response_pair_scores(0.95)
    # above the threshold the order flips: he answers from knowledge
    assert scores["letter_discussion"] > scores["denial"]


# ---------------- the generic candidate generator ----------------

def test_keeper_candidates_are_goal_driven():
    scenario = load_scenario(WAREHOUSE)
    cfg = load_all(VARIANT_ROOT)["keeper"]
    provider = RuleProvider(cfg, seed=42)
    provider.scenario = scenario
    agent = CharacterAgent(cfg, provider, scenario=scenario)
    # B2 note: find_missing_key starts INACTIVE (activation
    # condition); the runner's init evaluation activates it from his
    # private knowledge — mirror that here
    agent.evaluate_goals(0, cause="init")
    ctx = context_builder.build_context(agent, perc(1, "bar"))
    result = asyncio.run(provider.generate_decision(ctx))
    scores = result.candidate_scores
    assert scores is not None and scores["candidates"]
    goal_cands = [c for c in scores["candidates"]
                  if c["goal_key"] == "find_missing_key"]
    assert goal_cands, "no find_missing_key-driven candidate in the top-5"
    chosen = result.decision.action
    assert context_builder.action_is_offered(
        chosen.model_dump(), ctx.available_actions)


def test_synthesizer_links_plan_to_goal_with_conditions():
    cfg = load_all()["soldier"]
    provider = RuleProvider(cfg, seed=42)
    agent = CharacterAgent(cfg, provider)
    ctx = context_builder.build_context(agent, perc(1, "door"))
    result = asyncio.run(provider.generate_decision(ctx))
    pu = result.decision.plan_update
    assert pu.status == "replace" and pu.goal_id == "complete_investigation"
    assert pu.new_steps and all(s.success_condition for s in pu.new_steps)
    # and once a plan serves the goal, no re-synthesis
    agent.plan_manager.set_plan(agent.state, "complete_investigation",
                                [s.model_dump() for s in pu.new_steps],
                                tick=1, reason="test",
                                goal_id="complete_investigation")
    ctx2 = context_builder.build_context(agent, perc(2, "door"))
    result2 = asyncio.run(provider.generate_decision(ctx2))
    assert result2.decision.plan_update.status == "continue"


def test_candidate_scores_persisted_on_decisions(tmp_path):
    d = asyncio.run(run_simulation(mode="rule", ticks=3, seed=42,
                                   run_name="b1_scores", out_root=tmp_path))
    db = sqlite3.connect(f"file:{d}/acp.sqlite3?mode=ro", uri=True)
    rows = db.execute(
        "select candidate_scores from agent_decisions "
        "where candidate_scores is not null").fetchall()
    assert rows, "rule decisions must persist their candidate scores"
    parsed = json.loads(rows[0][0])
    assert parsed["chosen"] and parsed["candidates"]
    top = parsed["candidates"][0]
    assert "factors" in top and "goal" in top["factors"]
