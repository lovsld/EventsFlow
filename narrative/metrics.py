"""Unified Phase 1 quality metrics (task book §8.2).

Each metric is computed by a stated method from stated sources;
metrics that rest on LLM judgement are labelled as such and are
never presented as objective fact. Human-sampling fields are
left as explicit nulls for a reviewer to fill — the module
does not fabricate them.
"""
from __future__ import annotations

import json
from pathlib import Path

from narrative.fbudget import analyze as f_analyze
from narrative.material import detect_log_leakage
from narrative.scene_records import build_scene_records


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def compute_metrics(run_dir: Path, narrative_dir: Path,
                    judge_summary: dict | None = None,
                    novel_path: Path | None = None,
                    quality_path: Path | None = None) -> dict:
    upgrade = narrative_dir / "upgrade"
    novel = _load(novel_path or (upgrade / "trace_v2.json"))
    observations = _load(narrative_dir / "observations.json")
    paras = [p for ch in novel["chapters"]
             for p in ch["paragraphs"]]
    phase1 = narrative_dir / "phase1"
    out: dict = {}
    # 1. Story Progress Rate — decision_quality judge summary
    if judge_summary is not None:
        out["story_progress_rate"] = {
            "value": round(judge_summary["progress_ticks"]
                           / max(1, judge_summary["ticks"]), 4),
            "final_story_progress":
                judge_summary["final_story_progress"],
            "method": "ticks advancing >=1 of six dimensions / "
                      "total ticks; final progress = mean over "
                      "questions (answered 1 / addressed 0.5 / "
                      "open 0)",
            "source": "v03.decision_quality.judge_run"}
    # 2. Event Traceability
    ne_ids = {e["source_event_ids"][0] for e in
              _load(upgrade / "narrative_events.json")}
    f1 = [p for p in paras if p.get("level") == "F1"]
    f1_ok = [p for p in f1 if p.get("event_ids") and
             set(p["event_ids"]) <= ne_ids]
    out["event_traceability"] = {
        "value": round(len(f1_ok) / max(1, len(f1)), 4),
        "method": "F1 paragraphs whose event_ids all resolve to "
                  "real events / all F1 paragraphs",
        "source": "trace_v2 + narrative_events"}
    # 3. System Log Leakage
    leaks = detect_log_leakage(paras, observations)
    out["system_log_leakage"] = {
        "value": len(leaks),
        "rate": round(len(leaks) / max(1, len(paras)), 4),
        "method": "quoted spans equal to a template summary or "
                  "matching the `word @ word` log pattern",
        "source": "narrative.material.detect_log_leakage"}
    # 4. Scene Repetition Rate
    rec = build_scene_records(narrative_dir, novel)
    out["scene_repetition_rate"] = {
        "value": rec["summary"]["scene_repetition_rate"],
        "method": "valueless-repeat paragraphs (similar wording, "
                  "no new event, no new fact) / all paragraphs; "
                  "layer-3 narrative-value judgement requires "
                  "human sampling",
        "human_sampling": None,
        "source": "narrative.scene_records"}
    # 5/6. Emotional & Motivation Coverage (inference layer)
    psych_path = phase1 / "psychology.json"
    if psych_path.exists():
        psych = _load(psych_path)
        emos = psych["emotions"]
        known = sum(1 for r in emos if r["emotion"] != "unknown")
        out["emotional_coverage"] = {
            "value": round(known / max(1, len(emos)), 4),
            "method": "character x scene records with a "
                      "non-unknown emotion / all records",
            "source": "phase1/psychology.json (inference layer)"}
        d = psych["diagnostics"]
        out["motivation_coverage"] = {
            "value": round(d["motivated_actions"]
                           / max(1, d["key_actions"]), 4),
            "method": "key actions (importance >= 0.5 speech/"
                      "search/confront) carrying >=1 MOTIVATED_BY "
                      "hypothesis / all key actions",
            "source": "phase1/psychology.json (inference layer)"}
    # 7. F3 Ratio (+ full budget check)
    fb = f_analyze(novel)
    out["f3_ratio"] = {"value": fb["book"]["ratios"]["F3"],
                       "over_budget": fb["over_budget"],
                       "issues": fb["issues"],
                       "method": "F3 paragraphs / all paragraphs",
                       "source": "narrative.fbudget"}
    # 8/9. Fact Consistency & Dialogue Naturalness — from the
    # stored quality report; dialogue naturalness is an LLM
    # reviewer score and is labelled accordingly.
    qpath = quality_path or (upgrade
                             / "narrative_quality_report.json")
    if qpath.exists():
        q = _load(qpath)
        out["fact_consistency"] = {
            "value": q["scores"].get("factual_consistency"),
            "method": "deterministic: 5 - violations (non-"
                      "verbatim quotes, F1 without ids, log "
                      "leakage)",
            "source": str(qpath.relative_to(narrative_dir))}
        out["dialogue_naturalness"] = {
            "value": q["scores"].get("dialogue_naturalness"),
            "method": "LLM reviewer score (independent reviewer "
                      "role) — judgement, NOT objective fact",
            "evaluator": q.get("evaluator"),
            "source": str(qpath.relative_to(narrative_dir))}
    return out
