"""V0.3 Phase 5 acceptance tests (task book §19 Phase 5):
plans generated, exports complete, continuity validation works
(catches planted violations, passes the canonical run).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from v03.graph.models import EventGraph, GraphNode  # noqa: E402
from v03.planning.continuity import ContinuityValidator  # noqa: E402
from v03.planning.exporter import export_run  # noqa: E402
from v03.planning.planner import build_plan  # noqa: E402
from v03.story.architect import load_config  # noqa: E402

RUN = PROJECT / "runs_v02" / "rule_v02c4_seed42"


def test_arc_transitions_are_event_driven():
    cfg = load_config(PROJECT / "stories" / "tavern_secret.json")
    plan = build_plan(RUN, cfg, PROJECT / "characters_v02")
    stages = [t["to"] for t in plan["arc"]["transitions"]]
    assert stages == ["development", "escalation", "climax"]
    for t in plan["arc"]["transitions"]:
        assert t["trigger"] and t["tick"] > 0
    # unresolved run: honestly no resolution stage
    assert "resolution" in plan["arc"]["not_reached"]
    assert "aftermath" in plan["arc"]["not_reached"]


def test_chapters_cover_all_ticks():
    cfg = load_config(PROJECT / "stories" / "tavern_secret.json")
    plan = build_plan(RUN, cfg, PROJECT / "characters_v02")
    covered = []
    for ch in plan["chapters"]:
        covered += list(range(ch["tick_range"][0],
                              ch["tick_range"][1] + 1))
    assert covered == list(range(1, 21))
    setups = {s["id"]: s for s in plan["setups"]}
    letter_q = next(s for s in setups.values()
                    if s["kind"] == "question"
                    and s["payoff_tick"] is not None)
    assert letter_q["status"] == "paid_off"


def test_continuity_catches_planted_violations():
    v = ContinuityValidator(known_facts={"secret_fact"},
                            dependencies=[("dep", "src")])
    records = [
        {"tick": 1, "events": [
            {"event_id": "E0002", "type": "search",
             "actors": ["a"], "found": ["key"]}]},
        {"tick": 2, "events": [
            {"event_id": "E0001", "type": "search",
             "actors": ["b"], "found": ["key"]}]},
    ]
    graph = EventGraph(run_id="x", nodes=[
        GraphNode(event_id="E0002", tick=1, type="search"),
        GraphNode(event_id="E0001", tick=2, type="speak",
                  actors=["b"], proposition="secret_fact",
                  epistemic="unverified")])
    out = v.validate(records, graph, {"dep": 1, "src": 5})
    kinds = {i["kind"] for i in out["issues"]}
    assert "event_id_order" in kinds          # E0001 after E0002
    assert "duplicate_find" in kinds          # key found twice
    assert "knowledge_use_before_learning" in kinds
    assert "dependency_inversion" in kinds    # dep before src
    assert out["errors"] >= 4


def test_export_complete(tmp_path):
    cfg = load_config(PROJECT / "stories" / "tavern_secret.json")
    ex = export_run(RUN, cfg, PROJECT / "characters_v02",
                    out_dir=tmp_path)
    assert (tmp_path / "novel_export.json").exists()
    md = (tmp_path / "novel_export.md").read_text(encoding="utf-8")
    assert "故事弧线" in md and "伏笔与回收" in md
    assert len(ex["scene_packages"]) == 4
    assert len(ex["event_graph"]["nodes"]) == 73
    assert ex["continuity"]["errors"] == 0
    assert ex["plan"]["edge_counts"]["escalates"] == 5
