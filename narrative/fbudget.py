"""F1/F2/F3 budget control (Phase 1 / V1.0, task 5).

Level semantics are the project's existing ones (writer2):
F1 factual (event-anchored), F2 narrative presentation,
F3 creative addition. This module only MEASURES and DIAGNOSES:
per-chapter and whole-book ratios against a configurable
budget (narrative/f_budgets.json), over-budget warnings, and
F3->F2 downgrade candidates (F3 paragraphs that are anchored
to events and could be re-anchored as presentation). It never
deletes or relabels text: exceptions are a human decision
(task book §7.3).
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_BUDGETS = Path(__file__).with_name("f_budgets.json")


def load_budgets(path: Path | None = None) -> dict:
    """Load budgets, normalising the nested schema
    ({F1:{min}, F2:{min,max}, F3:{target_min,max,min_mode}})
    into the flat internal keys. The legacy flat schema
    (F1_min / F2_min / F2_max / F3_max) is still accepted.
    F3_target_min is a SOFT target (external review,
    2026-11-26): falling below it is reported, never treated
    as a hard over-budget violation."""
    raw = json.loads((path or DEFAULT_BUDGETS).read_text(
        encoding="utf-8"))
    if "F1_min" in raw:
        raw.setdefault("F3_target_min", None)
        return raw
    return {"F1_min": raw["F1"]["min"],
            "F2_min": raw["F2"]["min"],
            "F2_max": raw["F2"]["max"],
            "F3_max": raw["F3"]["max"],
            "F3_target_min": raw["F3"].get("target_min"),
            "F3_min_mode": raw["F3"].get("min_mode", "soft"),
            "note": raw.get("note", "")}


def _ratios(paras: list[dict]) -> dict:
    counts = {"F1": 0, "F2": 0, "F3": 0}
    for p in paras:
        if p.get("level") in counts:
            counts[p["level"]] += 1
    total = max(1, sum(counts.values()))
    return {"counts": counts,
            "ratios": {k: round(v / total, 4)
                       for k, v in counts.items()},
            "total": sum(counts.values())}


def analyze(novel: dict, budgets: dict | None = None) -> dict:
    b = budgets or load_budgets()
    chapters = []
    all_paras = []
    for ch in novel["chapters"]:
        paras = ch["paragraphs"]
        all_paras.extend(paras)
        chapters.append({"chapter_id": ch["chapter_id"],
                         **_ratios(paras)})
    book = _ratios(all_paras)
    issues = []
    r = book["ratios"]
    if r["F1"] < b["F1_min"]:
        issues.append({"kind": "F1_BELOW_MIN", "value": r["F1"],
                       "limit": b["F1_min"]})
    if r["F3"] > b["F3_max"]:
        issues.append({"kind": "F3_OVER_MAX", "value": r["F3"],
                       "limit": b["F3_max"]})
    if not (b["F2_min"] <= r["F2"] <= b["F2_max"]):
        issues.append({"kind": "F2_OUT_OF_RANGE", "value": r["F2"],
                       "limit": [b["F2_min"], b["F2_max"]]})
    soft_issues = []
    target = b.get("F3_target_min")
    if target is not None and r["F3"] < target:
        soft_issues.append({
            "kind": "F3_BELOW_TARGET", "severity": "soft",
            "value": r["F3"], "limit": target,
            "note": "soft target: allowed when no qualified F3 "
                    "exists; the reason must be recorded, and "
                    "filler must never be generated to reach it"})
    # downgrade candidates: F3 paragraphs anchored to events —
    # their content is event-bound presentation wearing an F3
    # label; a human may relabel them F2 (never automatic).
    candidates = [{"chapter_id": p.get("chapter_id"),
                   "event_ids": p.get("event_ids", []),
                   "text_prefix": p.get("text", "")[:50]}
                  for p in all_paras
                  if p.get("level") == "F3" and p.get("event_ids")]
    return {"book": book, "chapters": chapters,
            "budgets": {k: b.get(k) for k in
                        ("F1_min", "F2_min", "F2_max", "F3_max",
                         "F3_target_min")},
            "over_budget": bool(issues), "issues": issues,
            "soft_issues": soft_issues,
            "f3_downgrade_candidates": candidates,
            "fact_sources": {
                lvl: sorted({eid for p in all_paras
                             if p.get("level") == lvl
                             for eid in p.get("event_ids", [])})
                for lvl in ("F1", "F2", "F3")}}
