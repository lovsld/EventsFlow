"""V0.3 Phase 6 acceptance: the experiment matrix ran, the
autonomy red line held everywhere, and the budget cap held."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

PHASE6 = PROJECT / "v03_runs" / "phase6"


def _rows():
    return json.loads((PHASE6 / "STORY_METRICS.json")
                      .read_text(encoding="utf-8"))


def test_matrix_complete():
    rows = _rows()
    assert len(rows) == 12
    providers = {(r["provider"], r["engine_on"]) for r in rows}
    assert providers == {("rule", True), ("rule", False),
                         ("deepseek", True), ("deepseek", False)}


def test_autonomy_red_line_zero_everywhere():
    for r in _rows():
        assert r["story_engine_decision_changes"] == 0
        assert r["continuity_errors"] == 0


def test_rule_on_runs_injected():
    for r in _rows():
        if r["provider"] == "rule" and r["engine_on"]:
            assert r["injected_committed"] >= 2
            assert r["injected_perceived"] >= 1


def test_llm_budget_within_cap():
    rows = [r for r in _rows() if r["provider"] == "deepseek"]
    total_in = sum(r["usage"]["input_tokens"] for r in rows)
    total_out = sum(r["usage"]["output_tokens"] for r in rows)
    assert 0 < total_in <= 1_200_000
    assert 0 < total_out <= 150_000


def test_flagship_artifacts_exist():
    for name in ("NARRATIVE_EVENT_GRAPH.json", "SCENE_PACKAGES.json",
                 "NARRATIVE_PLAN.json"):
        data = json.loads((PHASE6 / name).read_text(encoding="utf-8"))
        assert data
