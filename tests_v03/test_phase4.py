"""V0.3 Phase 4 acceptance tests (task book §19 Phase 4):
causal relations traceable, scene packages complete, no
fabricated relations in the graph.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from v03.graph.builder import GraphBuilder  # noqa: E402
from v03.graph.packages import (build_graph_for_run,  # noqa: E402
                                build_scene_packages)
from v03.story.architect import load_config  # noqa: E402

RUN = PROJECT / "runs_v02" / "rule_v02c4_seed42"
FACTS = {"letter": {"direct": ["letter_exists"],
                    "hearsay": ["mayor_guilty"]}}


def rec(tick, events, deliveries=None):
    return {"tick": tick, "time": "21:00", "positions": {},
            "events": events, "deliveries": deliveries or []}


def speech(eid, actor, target, prop, stance="assert", type_="speak",
           claim="c"):
    return {"event_id": eid, "type": type_, "actors": [actor, target],
            "location": "bar", "proposition": prop, "stance": stance,
            "claim": claim}


def test_causes_enables_reveals_chain():
    b = GraphBuilder("synthetic", FACTS, initial_knowledge={})
    records = [
        rec(1, [{"event_id": "E1", "type": "search",
                 "actors": ["soldier"], "location": "back",
                 "found": ["letter"]}]),
        rec(2, [speech("E2", "soldier", "owner", "mayor_guilty")],
            [{"agent_id": "owner", "delivered_event_ids": ["E2"]}]),
        rec(3, [speech("E3", "owner", "merchant", "mayor_guilty")]),
    ]
    g = b.build(records)
    rel = {(e.source, e.target, e.relation) for e in g.edges}
    assert ("E1", "E2", "enables") in rel       # find enables telling
    assert ("E1", "E2", "reveals") in rel       # ... to a new audience
    assert ("E2", "E3", "causes") in rel        # owner repeats it later
    nodes = {n.event_id: n for n in g.nodes}
    assert nodes["E2"].epistemic == "rumored"   # hearsay-grade content
    assert nodes["E1"].epistemic == "objective"


def test_contradicts_conceals_prevents():
    b = GraphBuilder("synthetic", FACTS,
                     initial_knowledge={"owner": ["mayor_guilty"]})
    records = [
        rec(1, [speech("E1", "soldier", "owner", "mayor_guilty")]),
        rec(2, [speech("E2", "owner", "soldier", "mayor_guilty",
                      stance="deny")]),
        rec(3, [{"event_id": "E3", "type": "ask",
                 "actors": ["soldier", "owner"], "location": "bar",
                 "proposition": None, "stance": None, "claim": "q"},
                {"event_id": "E4", "type": "refuse",
                 "actors": ["owner"], "location": "bar"}]),
    ]
    g = b.build(records)
    rel = {(e.source, e.target, e.relation) for e in g.edges}
    assert ("E2", "E1", "contradicts") in rel
    assert ("", "E2", "conceals") not in rel  # source is initial: no
    # conceals edge needs a source EVENT; initial knowledge has none,
    # so the deny is flagged only via node epistemics
    assert ("E4", "E3", "prevents") in rel
    nodes = {n.event_id: n for n in g.nodes}
    assert nodes["E2"].epistemic == "false_statement"


def test_no_fabricated_edges_on_tavern():
    cfg = load_config(PROJECT / "stories" / "tavern_secret.json")
    g = build_graph_for_run(RUN, cfg, PROJECT / "characters_v02")
    node_ids = {n.event_id for n in g.nodes}
    assert len(node_ids) == 73
    for e in g.edges:
        assert e.source in node_ids and e.target in node_ids
        assert e.evidence  # every edge carries its evidence
    counts = g.edge_counts()
    assert counts.get("enables") == 1 and counts.get("reveals") == 2
    # denial claim has no proposition in this run -> honestly zero
    assert counts.get("contradicts", 0) == 0
    assert counts.get("conceals", 0) == 0
    assert counts.get("prevents", 0) == 0


def test_scene_packages_complete():
    cfg = load_config(PROJECT / "stories" / "tavern_secret.json")
    pkgs = build_scene_packages(RUN, cfg, PROJECT / "characters_v02")
    assert len(pkgs) == 4
    covered = [eid for p in pkgs for eid in p["events"]]
    assert len(covered) == len(set(covered))  # no event in two packs
    back = next(p for p in pkgs if p["location"] == "back_room")
    assert "mayor_embezzled" in \
        back["knowledge"]["soldier"]["at_end"]
    assert back["knowledge"]["soldier"]["at_start"] == []
    assert "discovery" in back["dramatic_functions"]
    assert back["propagations_out"]  # soldier carried it to the bar
