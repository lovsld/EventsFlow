"""Phase 1 tasks 5+8: F budgets diagnose (never delete), and
the unified metrics carry methods + honest labels."""
from __future__ import annotations

import json
from pathlib import Path

from narrative.fbudget import analyze, load_budgets
from narrative.metrics import compute_metrics

ROOT = Path(__file__).resolve().parent.parent


def _novel(run: str) -> dict:
    return json.loads((ROOT / "narrative_runs" / run / "upgrade"
                       / "trace_v2.json").read_text(
                           encoding="utf-8"))


def test_baseline_novels_are_over_f3_budget_and_diagnosed():
    for run in ("rule_v02c4_seed42", "c5_lighthouse_seed7",
                "clinic_v02cfix_seed42"):
        rep = analyze(_novel(run))
        assert rep["book"]["ratios"]["F3"] > 0.35  # ~40% baseline
        assert rep["over_budget"] is True
        assert any(i["kind"] == "F3_OVER_MAX"
                   for i in rep["issues"])
        # diagnosis only: the analysis returns candidates, it
        # does not alter the novel
        assert rep["book"]["total"] == sum(
            rep["book"]["counts"].values())


def test_budgets_are_config_not_code():
    b = load_budgets()
    assert b["F3_max"] == 0.20 and b["F1_min"] == 0.30
    custom = dict(b, F3_max=0.99)
    rep = analyze(_novel("rule_v02c4_seed42"), budgets=custom)
    assert not any(i["kind"] == "F3_OVER_MAX"
                   for i in rep["issues"])


def test_metrics_have_methods_and_llm_labels():
    ndir = ROOT / "narrative_runs" / "clinic_v02cfix_seed42"
    m = compute_metrics(ROOT / "runs_v02" / "clinic_v02cfix_seed42",
                        ndir)
    assert m["system_log_leakage"]["value"] == 2  # baseline novel
    assert m["event_traceability"]["value"] == 1.0
    assert "NOT objective fact" in \
        m["dialogue_naturalness"]["method"]
    assert m["scene_repetition_rate"]["human_sampling"] is None
    for metric in m.values():
        assert metric["method"] and metric["source"]
