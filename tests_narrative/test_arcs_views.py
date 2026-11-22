"""Upgrade Phase 3: character arcs (gated turning points) and
thread/conflict views (dual calibre, severity != tension)."""
from __future__ import annotations

import json
from pathlib import Path

from narrative.character_arc import build_character_arcs
from narrative.story_views import build_story_views

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = {"rule_v02c4_seed42": "stories/tavern_secret.json",
           "c5_lighthouse_seed7": "stories/lighthouse_watch.json",
           "clinic_v02cfix_seed42": "stories/clinic_fever.json"}


def _dirs(name: str):
    return (ROOT / "runs_v02" / name, ROOT / "narrative_runs" / name,
            ROOT / "v03_runs" / name, ROOT / CONFIGS[name])


def test_soldier_turning_point_passes_the_gate():
    run_dir, ndir, v03, cfg = _dirs("rule_v02c4_seed42")
    result = build_character_arcs(run_dir, ndir, v03, cfg)
    arcs = {a["character_id"]: a for a in result["arcs"]}
    soldier = arcs["soldier"]
    facts = {t["fact"] for t in soldier["turning_points"]}
    assert "mayor_embezzled" in facts
    for t in soldier["turning_points"]:
        assert t["downstream_uses"], "a turning point without " \
                                      "downstream use must not pass"
    # every character's arc stays open; no valence auto-assigned
    for a in result["arcs"]:
        assert a["arc_status"] == "open"
        assert a["character_arc_type"] in ("unresolved_arc",
                                           "flat_arc")


def test_thread_view_keeps_dual_calibre_and_open_question():
    run_dir, ndir, v03, _ = _dirs("rule_v02c4_seed42")
    views = build_story_views(run_dir, ndir, v03)
    main = next(t for t in views["threads"]
                if t["thread_id"] == "main_embezzlement")
    assert main["status"] != "resolved"
    assert any("侵吞" in q for q in main["unresolved_questions"])
    diag = views["diagnostics"]["main_embezzlement"]
    assert diag["v03_progress"] == 0.5


def test_conflict_view_severity_and_tension_are_separate():
    run_dir, ndir, v03, _ = _dirs("rule_v02c4_seed42")
    views = build_story_views(run_dir, ndir, v03)
    assert views["conflicts"]
    c = views["conflicts"][0]
    assert c["severity"] > 0 and "tension" in c
    assert c["resolution_conditions"], "open questions of related "\
                                       "threads become conditions"


def test_views_and_arcs_all_scenarios():
    for name in CONFIGS:
        run_dir, ndir, v03, cfg = _dirs(name)
        arcs = build_character_arcs(run_dir, ndir, v03, cfg)
        assert len(arcs["arcs"]) == 3
        views = build_story_views(run_dir, ndir, v03)
        assert views["threads"]
        for t in views["threads"]:
            assert t["status"] != "resolved" or \
                t["unresolved_questions"] == []
