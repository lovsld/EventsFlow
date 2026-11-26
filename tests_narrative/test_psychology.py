"""Phase 1 task 4: psychology inference layer — emotions with
evidence, MOTIVATED_BY hypotheses, physical separation from the
verified causal graph."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from narrative.psychology import build_psychology

ROOT = Path(__file__).resolve().parent.parent
RUNS = ("rule_v02c4_seed42", "c5_lighthouse_seed7",
        "clinic_v02cfix_seed42")


def test_emotions_have_evidence_or_are_unknown():
    for run in RUNS:
        psych = build_psychology(ROOT / "narrative_runs" / run)
        assert psych["emotions"]
        for r in psych["emotions"]:
            assert r["evidence_level"] == "interpretation"
            assert r["source_type"] == "rule_inference"
            if r["emotion"] == "unknown":
                assert r["confidence"] == 0.0
                assert r["evidence_event_ids"] == []
            else:
                assert r["evidence_event_ids"], (run, r)
                assert 0 < r["confidence"] <= 1
        # emotions actually vary across scenes in at least the
        # tavern run (not a single constant label)
    psych = build_psychology(
        ROOT / "narrative_runs" / "rule_v02c4_seed42")
    kinds = {r["emotion"] for r in psych["emotions"]}
    assert len(kinds) >= 3


def test_motivated_by_produced_with_hypothesis_discipline():
    for run in RUNS:
        psych = build_psychology(ROOT / "narrative_runs" / run)
        mb = psych["motivated_by"]
        assert mb, run  # was zero before Phase 1
        for m in mb:
            assert m["inference_status"] in (
                "HYPOTHESIS", "SUPPORTED", "CONTRADICTED",
                "UNRESOLVED")
            assert m["alternative_explanations"]
            assert m["confidence"] <= 0.6
            if m["inference_status"] == "SUPPORTED":
                assert len(m["evidence_event_ids"]) >= 2


def test_causal_graph_file_untouched_by_psychology():
    ndir = ROOT / "narrative_runs" / "rule_v02c4_seed42"
    graph = ndir / "upgrade" / "causal_graph.json"
    before = hashlib.md5(graph.read_bytes()).hexdigest()
    build_psychology(ndir)
    after = hashlib.md5(graph.read_bytes()).hexdigest()
    assert before == after
    # and no MOTIVATED_BY edge leaks into the verified graph
    data = json.loads(graph.read_text(encoding="utf-8"))
    rels = {e.get("relation") for e in data.get("edges", [])}
    assert "MOTIVATED_BY" not in rels
