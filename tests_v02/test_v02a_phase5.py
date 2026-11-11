"""V0.2-A Phase 5 tests: the extended experiment metrics, the
world-config externalization (experiment 3a equivalence), and the
variant scenario smoke run (experiment 3b — pure data, no decision
code touched).
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from v02 import broker, context_builder, world
from v02.experiments import collect_run_metrics
from v02.runner import run_simulation
from v02.world_config import DEFAULT_WORLD, adjacency_of

ROOT = Path(__file__).resolve().parent.parent


# ---------- metrics ----------

def test_metrics_on_fresh_rule_run(tmp_path):
    d = asyncio.run(run_simulation(mode="rule", ticks=6, seed=42,
                                   run_name="m", out_root=tmp_path))
    m = collect_run_metrics(d)
    v = m["v02a"]
    snaps = json.loads((d / "world_log.json").read_text(encoding="utf-8"))
    dialogue = sum(1 for snap in snaps for e in snap["events"]
                   if e["type"] in ("speak", "ask", "confront"))
    assert v["dialogue_count"] == dialogue
    assert 0 < v["action_success_rate"] <= 1
    assert 0 <= v["repeat_action_rate"] <= 1
    assert v["letter_knowledge_coverage"]["agents"] == 3
    assert v["goal_completion_rate"] is not None   # lifecycle states exist
    assert v["llm"]["calls"] == 0                  # rule mode: no LLM
    assert v["llm"]["latency_ms"] is None          # never fabricated


def test_metrics_on_pre_v02a_run_report_gaps_honestly(tmp_path):
    # a COPY of the pre-V0.2-A canonical run: opening a Store creates
    # any missing tables, so the original artefact stays untouched
    src = ROOT / "runs_v02" / "rule_planlayer_seed42"
    dst = tmp_path / "old_run"
    shutil.copytree(src, dst)
    m = collect_run_metrics(dst)
    v = m["v02a"]
    # the old state.json goals carry no lifecycle status...
    assert "unspecified_pre_lifecycle" in v["goals_by_status"]
    assert v["goal_completion_rate"] is None       # not measurable, not 0
    # ...and its summary predates the repair counter
    assert v["llm"]["repairs_total"] is None
    assert v["llm"]["json_repair_rate"] is None


# ---------- experiment 3a: one definition of the world ----------

def test_adjacency_has_a_single_source():
    assert world.ADJACENCY == adjacency_of()
    assert broker.ADJACENCY == adjacency_of()
    assert context_builder.ADJACENCY == adjacency_of()


def test_world_engine_explicit_default_config_is_identical():
    from v02.config import load_all
    from v02.world import WorldEngine
    configs = load_all()
    a = WorldEngine(configs, seed=42)
    b = WorldEngine(configs, seed=42, world_config=DEFAULT_WORLD)
    assert a.state == b.state


def test_resolver_search_and_show_are_data_driven():
    """The generalized resolver semantics on a toy world: search
    finds whatever object is objectively there; show works for any
    held object — no letter-specific code path remains."""
    from v02.interaction import InteractionManager
    from v02.world import resolve
    import random
    w = {"tick": 1,
         "agents": {"a": {"location": "back_room", "initiative": 0.5},
                    "b": {"location": "back_room", "initiative": 0.5}},
         "objects": {"warehouse_key": {"holder": "a",
                                       "location": "back_room",
                                       "seen_by": []}},
         "last_action_results": {}}
    seq = [0]
    events = resolve(w, {"a": {"verb": "search",
                                "target_location": "back_room"}},
                     random.Random(1), seq, InteractionManager(),
                     DEFAULT_WORLD)
    assert events[0]["found"] == ["warehouse_key"]
    assert w["objects"]["warehouse_key"]["seen_by"] == ["a"]
    events = resolve(w, {"a": {"verb": "show", "object": "warehouse_key",
                                "target": "b"}},
                     random.Random(1), seq, InteractionManager(),
                     DEFAULT_WORLD)
    assert events[0]["type"] == "show"
    assert w["objects"]["warehouse_key"]["seen_by"] == ["a", "b"]


# ---------- experiment 3b: variant scenario from data only ----------

def test_variant_scenario_runs_and_keeps_local_cognition(tmp_path):
    world_cfg = json.loads(
        (ROOT / "scenarios" / "warehouse_key_world.json")
        .read_text(encoding="utf-8"))
    d = asyncio.run(run_simulation(
        mode="rule", ticks=8, seed=42, run_name="variant_smoke",
        out_root=tmp_path, characters_root=ROOT / "characters_variant",
        world_config=world_cfg))
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    assert set(s["final_positions"]) == {"keeper", "traveler", "apprentice"}
    assert s["total_events"] > 0
    # nobody can know what they never experienced: final beliefs are
    # exactly the characters' private knowledge, nothing about the
    # key's objective whereabouts leaked into anyone's head
    private = {"keeper": {"key_missing_this_morning"},
               "traveler": {"goods_stored_here"},
               "apprentice": {"heard_movement_last_night"}}
    for cid, keys in private.items():
        state = json.loads(
            (d / "characters" / cid / "state.json").read_text(
                encoding="utf-8"))
        assert {b["key"] for b in state["beliefs"]} == keys
    assert s["objects_seen_by"]["warehouse_key"] == ["keeper"]
