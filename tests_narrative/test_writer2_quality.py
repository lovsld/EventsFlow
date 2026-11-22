"""Upgrade Phase 6: writer2 context assembly + quality metrics,
exercised with a stub generator (no live calls in tests)."""
from __future__ import annotations

import json
from pathlib import Path

from narrative.quality import deterministic_scores, evaluate
from narrative.writer2 import assemble_context, write_novel

ROOT = Path(__file__).resolve().parent.parent


def _stub(system: str, user: str, **kw):
    if "评审" in system:
        return json.dumps(
            {"scores": {"causal_coherence": 4,
                        "character_consistency": 4,
                        "arc_plausibility": 3,
                        "emotional_variation": 3,
                        "dialogue_naturalness": 4,
                        "readability": 4},
             "issues": []}), {"prompt_tokens": 10,
                              "candidates_tokens": 5}
    return json.dumps(
        {"paragraphs": [
            {"level": "F1", "event_ids": [],
             "text": "占位事实段。"},
            {"level": "F3", "event_ids": [],
             "text": "占位氛围段。"}],
         "scene_end_state": "局面未变"}), {"prompt_tokens": 10,
                                          "candidates_tokens": 5}


def _products(name: str):
    ndir = ROOT / "narrative_runs" / name
    plan = json.loads((ndir / "upgrade" / "plan.json")
                      .read_text(encoding="utf-8"))
    products = {
        "events": json.loads((ndir / "upgrade" /
                              "narrative_events.json")
                             .read_text(encoding="utf-8")),
        "observations": json.loads((ndir / "observations.json")
                                   .read_text(encoding="utf-8")),
        "semantic": json.loads((ndir / "upgrade" /
                                "semantic.json")
                               .read_text(encoding="utf-8")),
        "arcs": json.loads((ndir / "upgrade" /
                            "character_arcs.json")
                           .read_text(encoding="utf-8")),
        "views": json.loads((ndir / "upgrade" /
                             "story_views.json")
                            .read_text(encoding="utf-8")),
        "setup": json.loads((ndir / "upgrade" /
                             "setup_payoff.json")
                            .read_text(encoding="utf-8"))}
    return plan, products


def test_context_manifest_nine_inputs():
    run_dir = ROOT / "runs_v02" / "rule_v02c4_seed42"
    ndir = ROOT / "narrative_runs" / "rule_v02c4_seed42"
    plan, products = _products("rule_v02c4_seed42")
    scene = plan["scenes"][1]
    assembled = assemble_context(run_dir, ndir, scene, plan,
                                 products, [])
    ctx = assembled["context"]
    assert ctx["scene_outline"] is scene
    assert ctx["story_outline"]
    assert ctx["knowledge_at_start"], "knowledge state must be "\
                                       "assembled per character"
    # repeat events are explicitly marked for the writer
    roles = {e["ne_id"]: e["role"] for e in ctx["scene_events"]}
    assert any(r.startswith("repeat") for r in roles.values()) \
        or len(roles) <= 3


def test_write_and_evaluate_with_stub():
    run_dir = ROOT / "runs_v02" / "clinic_v02cfix_seed42"
    ndir = ROOT / "narrative_runs" / "clinic_v02cfix_seed42"
    novel = write_novel(run_dir, ndir, generate_fn=_stub)
    assert novel["usage"]["calls"] > 0
    assert novel["creative_register"], "F3 paragraphs must be " \
                                        "registered"
    result = evaluate(run_dir, ndir, novel, generate_fn=_stub)
    scores = result["report"]["scores"]
    assert "factual_consistency" in scores
    assert "causal_coherence" in scores  # from the reviewer stub
    assert result["report"]["evaluator"]
    assert "old_story_B" in result["comparison"]


def test_deterministic_repetition_metric_catches_duplicates():
    run_dir = ROOT / "runs_v02" / "rule_v02c4_seed42"
    ndir = ROOT / "narrative_runs" / "rule_v02c4_seed42"
    novel = {"chapters": [{"chapter_id": "CH01", "paragraphs": [
        {"level": "F2", "event_ids": [],
         "text": "同一句话在这里出现了很多次需要检测。"},
        {"level": "F2", "event_ids": [],
         "text": "同一句话在这里出现了很多次需要检测。"},
        {"level": "F2", "event_ids": [],
         "text": "同一句话在这里出现了很多次需要检测。"}]}]}
    scores, _ = deterministic_scores(run_dir, ndir, novel)
    assert scores["repetition"] < 2.0
