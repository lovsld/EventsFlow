"""Upgrade Phase 1: NarrativeEvent schema + projection builder."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from narrative.models import NarrativeEvent
from narrative.models.build import (build_narrative_events,
                                    write_narrative_events)

ROOT = Path(__file__).resolve().parent.parent
RUNS = {"rule_v02c4_seed42": "rule_v02c4_seed42",
        "c5_lighthouse_seed7": "c5_lighthouse_seed7",
        "clinic_v02cfix_seed42": "clinic_v02cfix_seed42"}


def _run(name: str):
    return ROOT / "runs_v02" / name, ROOT / "narrative_runs" / name


def test_schema_requires_traceable_sources():
    ev = NarrativeEvent(narrative_event_id="NE-E0001",
                        source_event_ids=["E0001"], tick=1,
                        event_summary="x", event_type="speak")
    assert ev.evidence_level.value == "objective"
    assert ev.dramatic_function == []      # empty is legal
    with pytest.raises(Exception):
        NarrativeEvent(narrative_event_id="NE-X", tick=1,
                       event_summary="x")  # source ids required


def test_projection_tavern_discovery_and_knowledge():
    run_dir, ndir = _run("rule_v02c4_seed42")
    events = build_narrative_events(run_dir, ndir)
    assert len(events) == 73
    by_id = {e.narrative_event_id: e for e in events}
    find = by_id["NE-E0008"]
    assert "discovery" in [f.value for f in find.dramatic_function]
    facts = {k.fact for k in find.knowledge_changes
             if k.character == "soldier"}
    assert "mayor_embezzled" in facts
    # nobody learns a fact before it exists: every knowledge change
    # references its own event
    for e in events:
        for k in e.knowledge_changes:
            assert k.source_event_id in e.source_event_ids


def test_projection_deterministic_and_world_log_untouched(tmp_path):
    run_dir, ndir = _run("rule_v02c4_seed42")
    wl = run_dir / "world_log.json"
    before = hashlib.sha256(wl.read_bytes()).hexdigest()
    a = write_narrative_events(run_dir, ndir, tmp_path / "a")
    b = write_narrative_events(run_dir, ndir, tmp_path / "b")
    assert a.read_bytes() == b.read_bytes()
    assert hashlib.sha256(wl.read_bytes()).hexdigest() == before


def test_projection_all_three_scenarios():
    for name in RUNS:
        run_dir, ndir = _run(name)
        events = build_narrative_events(run_dir, ndir)
        obs = json.loads((ndir / "observations.json")
                         .read_text(encoding="utf-8"))
        assert len(events) == len(obs)
        assert all(0.0 <= e.narrative_importance <= 1.0
                   for e in events)
