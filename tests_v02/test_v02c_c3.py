"""V0.2-C C3 tests: unified Information Value + exploration.

Pure level first (the derived searched-places set, searched
certainty), then the two generation expansions (holder-leaf
search routes under a weak holds-belief; baseline exploration on
genuinely idle ticks), then the score-level IV for search.
Trajectory gates live in the C3 stage record
(docs/V0.2-C_IMPLEMENTATION_REPORT.md).
"""
from __future__ import annotations

import asyncio

from v02.conditions import SubjectiveView
from v02.config import load_all
from v02.context_builder import build_context
from v02.info_value import searched_certainty
from v02.providers.rule_provider import RuleProvider
from v02.scenario import default_scenario

SC = default_scenario()


def _view(agent_id="owner", own=(), empty=(), sightings=None,
          beliefs=None):
    from v02.protocol import Belief
    return SubjectiveView(
        agent_id=agent_id, tick=9, location="bar",
        beliefs={k: Belief(key=k, confidence=c, kind=kind)
                 for k, (c, kind) in (beliefs or {}).items()},
        searched_empty_places=set(empty),
        object_sightings=dict(sightings or {}),
        own_actions=list(own))


# ---------------- derived searched places (design 4.3) ----------------

def test_searched_places_derived_from_existing_state_only():
    view = _view(
        own=[{"type": "search", "target_location": "back_room",
              "tick": 3},
             {"type": "search", "target_location": "back_room",
              "tick": 7},
             {"type": "speak", "target": "soldier", "tick": 8}],
        empty=["cellar"],
        sightings={"anonymous_letter": "back_room"})
    places = view.searched_places
    assert places["back_room"] == 7          # latest search tick
    assert places["cellar"] == -1            # empty-record, no tick
    assert searched_certainty(view, "back_room") == 0.9
    assert searched_certainty(view, "bar") == 0.0


# ---------------- holder-leaf search route (design 4.2.1) ----------------

def _possession_cands(view, holder="merchant"):
    cfg = load_all()["owner"]
    provider = RuleProvider(cfg, seed=42)
    from v02.agent import CharacterAgent
    agent = CharacterAgent(cfg, provider)
    ctx = build_context(agent, {"tick": 9, "time": "21:00",
                                "location": "bar", "colocated": [],
                                "events": [], "muffled": [],
                                "visible_objects": []})
    leaf = {"type": "object_possession", "object": "anonymous_letter",
            "holder": holder}
    cands: list = []
    provider._leaf_candidates(cands, leaf, view, ctx, source="goal",
                              goal_key="g", counts={})
    return cands


def test_holder_leaf_searches_when_holds_belief_is_weak():
    view = _view(beliefs={"holds:merchant:anonymous_letter":
                          (0.4, "heard")})
    cands = _possession_cands(view)
    verbs = {c["action"]["type"] for c in cands}
    assert "search" in verbs or "move" in verbs


def test_holder_leaf_does_not_search_under_strong_holds_belief():
    view = _view(beliefs={"holds:merchant:anonymous_letter":
                          (0.95, "heard")})
    cands = _possession_cands(view)
    verbs = {c["action"]["type"] for c in cands}
    assert "search" not in verbs and "move" not in verbs


# ---------------- baseline exploration (design 4.2.2) ----------------

def _perc(tick=0, loc="bar"):
    return {"tick": tick, "time": "21:00", "location": loc,
            "colocated": [], "events": [], "muffled": [],
            "visible_objects": [], "agent_id": "owner",
            "world_public": {"weather": "clear"}}


def _idle_owner():
    from v02.agent import CharacterAgent
    cfg = load_all()["owner"]
    provider = RuleProvider(cfg, seed=42)
    agent = CharacterAgent(cfg, provider)
    # every achievement goal already settled: nothing generates a
    # goal-leaf candidate, no plan, no questions — a genuinely
    # idle tick for the exploration trigger
    for g in agent.state.goals:
        if g.kind == "achievement":
            g.status = "completed"
    agent.state.current_plan = None
    return agent, provider


def test_baseline_exploration_offers_unsearched_places_when_idle():
    agent, provider = _idle_owner()
    # standing in a searchable place: the exploration candidate
    # is a search itself (elsewhere it would be a move leg)
    ctx = build_context(agent, _perc(loc="back_room"))
    result = asyncio.run(provider.generate_decision(ctx))
    census = result.census
    # the four always-on baseline candidates (observe/listen/
    # wait/work) plus at least one exploration search/move
    assert census["by_source"].get("baseline", 0) >= 5
    assert census["search_generated"] >= 1


def test_baseline_exploration_silent_when_goals_generate():
    from v02.agent import CharacterAgent
    cfg = load_all()["owner"]
    provider = RuleProvider(cfg, seed=42)
    agent = CharacterAgent(cfg, provider)
    # activate the achievement goals by hand (decide() normally
    # runs the evaluator first): their leaves generate goal
    # candidates, so the idle-only trigger must stay silent
    for g in agent.state.goals:
        if g.kind == "achievement":
            g.status = "active"
    ctx = build_context(agent, _perc())
    result = asyncio.run(provider.generate_decision(ctx))
    # with live goals the exploration trigger stays silent: the
    # baseline source holds at most the four always-on candidates
    # (signature dedup against goal-source observe/listen can
    # only lower the count), and no baseline search is generated
    assert result.census["by_source"].get("baseline", 0) <= 4
    assert result.census["search_generated"] == 0
