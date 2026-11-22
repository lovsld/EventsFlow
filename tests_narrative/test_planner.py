"""Upgrade Phase 5: three-level planner + continuity validator."""
from __future__ import annotations

import json
from pathlib import Path

from narrative.initial_knowledge import seed_initial_knowledge
from narrative.planner import build_plan

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = {"rule_v02c4_seed42": "stories/tavern_secret.json",
           "c5_lighthouse_seed7": "stories/lighthouse_watch.json",
           "clinic_v02cfix_seed42": "stories/clinic_fever.json"}


def test_seed_initial_knowledge_from_state_files():
    known = seed_initial_knowledge(ROOT / "runs_v02" /
                                   "c5_lighthouse_seed7")
    assert known["keeper_zhou"] == ["ship_passing_tonight"]
    assert known["a_gui"] == ["jetty_lamp_out"]


def test_plans_validate_clean_and_stay_open():
    for name, cfg in CONFIGS.items():
        plan = build_plan(ROOT / "runs_v02" / name,
                          ROOT / "narrative_runs" / name,
                          ROOT / cfg)
        assert plan["validation"]["errors"] == [], \
            (name, plan["validation"]["errors"])
        assert plan["scenes"] and plan["chapters"]
        # scene integrity: chapters partition scene event lists
        # in order, no event twice
        seen: list[str] = []
        for ch in plan["chapters"]:
            seen += ch["included_events"]
        assert len(seen) == len(set(seen))
        # the outline never closes what the data left open
        assert "状态 resolved" not in plan["story"]["ending_state"]


def test_tavern_bar_is_not_one_giant_scene():
    plan = build_plan(ROOT / "runs_v02" / "rule_v02c4_seed42",
                      ROOT / "narrative_runs" / "rule_v02c4_seed42",
                      ROOT / CONFIGS["rule_v02c4_seed42"])
    sizes = [len(s["event_ids"]) for s in plan["scenes"]]
    assert max(sizes) < 38, sizes
    for s in plan["scenes"]:
        assert s["viewpoint_character"]
        assert s["scene_id"] and s["time_range"][0] <= \
            s["time_range"][1]
