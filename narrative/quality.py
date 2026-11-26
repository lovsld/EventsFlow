"""Narrative quality evaluation (upgrade Phase 6; design §6).

Twelve dimensions. Six are deterministic (computed here from
the trace and the upgrade products); six are literary and are
scored by an independent reviewer call — a different role
prompt from the writer's, per chapter, with anchored rubrics.
The evaluator is named in the report; the writer never grades
its own draft.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from narrative.gemini_client import MODEL as GEMINI_MODEL
from narrative.gemini_client import generate as _gemini_generate
from narrative.models.story import QualityReport

QUOTE_RE = re.compile(r"「([^」]{2,})」|“([^”]{2,})”|\"([^\"]{2,})\"")
LLM_DIMS = ["causal_coherence", "character_consistency",
            "arc_plausibility", "emotional_variation",
            "dialogue_naturalness", "readability"]
REVIEWER_SYSTEM = """你是一名独立的小说评审，与作者无关。按量表为每一章打分（1-5 整数）：
1=明显不成立，3=基本成立但有可见问题，5=成立且出色。
维度：causal_coherence 因果连贯（事件衔接有因果而非罗列）；character_consistency 人物一致（言行合其身份与已知信息）；arc_plausibility 人物弧线合理（变化有铺垫、不过度）；emotional_variation 情绪变化（有起伏而非平铺）；dialogue_naturalness 对话自然（人物语言有区分、不复读）；readability 文学可读性。
扣分必须给出具体问题（引用原文片段并指出问题）。未解决的悬念保持未解决是正确处理，不得因此扣分。
只输出 JSON：{"scores":{"causal_coherence":3,...},"issues":[{"dimension":"...","detail":"...","quote":"..."}]}"""


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[。！？\n]", text)
    return [p.strip() for p in parts if len(p.strip()) >= 8]


def deterministic_scores(run_dir: Path, narrative_dir: Path,
                         novel: dict) -> tuple[dict, list[dict]]:
    upgrade = narrative_dir / "upgrade"
    events = _load(upgrade / "narrative_events.json")
    observations = _load(narrative_dir / "observations.json")
    obs_text = {o["obs_id"]: (o.get("text") or "")
                for o in observations}
    ne_by_id = {e["narrative_event_id"]: e for e in events}
    plan = _load(upgrade / "plan.json")
    setup = _load(upgrade / "setup_payoff.json")
    views = _load(upgrade / "story_views.json")
    paras = [p for ch in novel["chapters"]
             for p in ch["paragraphs"]]
    issues: list[dict] = []
    scores: dict[str, float] = {}

    # factual consistency: F1 quotes verbatim + ids valid
    violations = 0
    for p in paras:
        if p.get("level") != "F1":
            continue
        ids = p.get("event_ids") or []
        if not ids:
            violations += 1
            issues.append({"dimension": "factual_consistency",
                           "severity": "high",
                           "detail": "F1 paragraph without "
                                     "event_ids",
                           "chapter_id": p.get("chapter_id"),
                           "event_ids": []})
            continue
        texts = [obs_text.get(i, "") for i in ids]
        for m in QUOTE_RE.finditer(p.get("text", "")):
            span = next(g for g in m.groups() if g)
            if not any(span in t for t in texts):
                violations += 1
                issues.append({
                    "dimension": "factual_consistency",
                    "severity": "high",
                    "detail": f"quote not verbatim: {span[:30]}",
                    "chapter_id": p.get("chapter_id"),
                    "event_ids": ids})
    scores["factual_consistency"] = max(
        0.0, 5.0 - 1.0 * violations)

    # system log leakage (Phase 1 task 2): a quoted log string in
    # ANY paragraph is a factual violation; F1 leaks were already
    # counted above as non-verbatim quotes, so only non-F1 leaks
    # add to the count here. All leaks are recorded as issues.
    from narrative.material import detect_log_leakage
    for leak in detect_log_leakage(paras, observations):
        issues.append({"dimension": "system_log_leakage",
                       "severity": "high",
                       "detail": f"system log string quoted: "
                                 f"{leak['span']}",
                       "chapter_id": leak["chapter_id"],
                       "event_ids": leak["event_ids"]})
    non_f1 = [p for p in paras if p.get("level") != "F1"]
    extra = len(detect_log_leakage(non_f1, observations))
    if extra:
        scores["factual_consistency"] = max(
            0.0, scores["factual_consistency"] - 1.0 * extra)

    # repetition: duplicate sentence rate in the draft
    all_text = "\n".join(p.get("text", "") for p in paras)
    sents = _sentences(all_text)
    dup_rate = 1 - len(set(sents)) / max(1, len(sents))
    scores["repetition"] = round(5.0 * (1 - dup_rate), 2)
    if dup_rate > 0.1:
        issues.append({"dimension": "repetition",
                       "severity": "medium",
                       "detail": f"duplicate sentence rate "
                                 f"{dup_rate:.2f}",
                       "chapter_id": "", "event_ids": []})

    # knowledge consistency: plan validation + F1 order inversions
    inversions = 0
    for ch in novel["chapters"]:
        last = -1
        for p in ch["paragraphs"]:
            ids = p.get("event_ids") or []
            if ids:
                tick = min(ne_by_id[f"NE-{i}"]["tick"]
                           for i in ids if f"NE-{i}" in ne_by_id)
                if tick < last:
                    inversions += 1
                last = max(last, tick)
    plan_errors = len(plan.get("validation", {}).get("errors", []))
    scores["knowledge_consistency"] = max(
        0.0, 5.0 - plan_errors - inversions)

    # setup/payoff: payoff records narrated at F1
    covered = {i for p in paras if p.get("level") == "F1"
               for i in (p.get("event_ids") or [])}
    payoffs = [r for r in setup.get("records", [])
               if r["status"] == "payoff"]
    if payoffs:
        hit = sum(1 for r in payoffs
                  if any(e.removeprefix("NE-") in covered
                         for e in r.get(
                             "actual_payoff_event_ids", [])))
        scores["setup_payoff"] = round(5.0 * hit / len(payoffs), 2)
    # else: dimension not applicable (no payoff exists to
    # narrate); omitted from scores, noted in issues
    else:
        issues.append({"dimension": "setup_payoff",
                       "severity": "info",
                       "detail": "no payoff records in this run; "
                                 "dimension not applicable",
                       "chapter_id": "", "event_ids": []})

    # scene completeness: turning points narrated at F1
    tps = [s["turning_point"] for s in plan.get("scenes", [])
           if s.get("turning_point")]
    if tps:
        hit = sum(1 for t in tps
                  if t.removeprefix("NE-") in covered)
        scores["scene_completeness"] = round(
            5.0 * hit / len(tps), 2)

    # chapter pacing: paragraph-count balance across chapters
    counts = [len(ch["paragraphs"]) for ch in novel["chapters"]]
    if len(counts) >= 2 and sum(counts) > 0:
        mean = sum(counts) / len(counts)
        var = sum((c - mean) ** 2 for c in counts) / len(counts)
        cv = (var ** 0.5) / max(1.0, mean)
        scores["chapter_pacing"] = round(
            max(0.0, 5.0 - 4.0 * cv), 2)

    # conflict progression (structural proxy): conflicts whose
    # related threads carry tension > 0 and an escalation history
    confs = views.get("conflicts", [])
    if confs:
        progressed = sum(1 for c in confs
                         if c.get("tension", 0) > 0 and
                         c.get("escalation_history"))
        scores["conflict_progression"] = round(
            5.0 * progressed / len(confs), 2)
    return scores, issues


def review_chapters(novel: dict, generate_fn=None) -> tuple[dict,
                                                           list,
                                                           dict]:
    gen = generate_fn or _gemini_generate
    totals: dict[str, list[int]] = {d: [] for d in LLM_DIMS}
    issues: list[dict] = []
    usage = {"prompt_tokens": 0, "candidates_tokens": 0,
             "calls": 0}
    for ch in novel["chapters"]:
        text = "\n".join(p.get("text", "")
                         for p in ch["paragraphs"])
        if not text.strip():
            continue
        out, u = gen(REVIEWER_SYSTEM,
                     f"【章节 {ch['chapter_id']}】\n{text}",
                     max_tokens=2048, temperature=0.3)
        usage["prompt_tokens"] += u.get("prompt_tokens", 0)
        usage["candidates_tokens"] += u.get("candidates_tokens", 0) + u.get("thoughts_tokens", 0)
        usage["calls"] += 1
        data = json.loads(out[out.find("{"):out.rfind("}") + 1])
        for dim, val in (data.get("scores") or {}).items():
            if dim in totals and isinstance(val, (int, float)):
                totals[dim].append(val)
        for iss in data.get("issues") or []:
            iss["chapter_id"] = ch["chapter_id"]
            iss.setdefault("event_ids", [])
            issues.append(iss)
    scores = {d: round(sum(v) / len(v), 2)
              for d, v in totals.items() if v}
    return scores, issues, usage


def old_draft_stats(narrative_dir: Path) -> dict:
    path = narrative_dir / "story_B.md"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    sents = _sentences(text)
    return {"chars": len(text), "sentences": len(sents),
            "duplicate_rate": round(
                1 - len(set(sents)) / max(1, len(sents)), 3)}


def evaluate(run_dir: Path, narrative_dir: Path, novel: dict,
             generate_fn=None, with_review: bool = True) -> dict:
    det, issues = deterministic_scores(run_dir, narrative_dir,
                                       novel)
    usage = {"prompt_tokens": 0, "candidates_tokens": 0,
             "calls": 0}
    scores = dict(det)
    if with_review:
        llm_scores, llm_issues, usage = review_chapters(
            novel, generate_fn=generate_fn)
        scores.update(llm_scores)
        issues += llm_issues
    report = QualityReport(
        run_id=run_dir.name, scores=scores, issues=issues,
        evaluator=f"{GEMINI_MODEL} (independent reviewer role) "
                  f"+ deterministic checks" if with_review else
                  "deterministic checks only")
    new_text = "\n".join(p.get("text", "") for ch in
                         novel["chapters"]
                         for p in ch["paragraphs"])
    sents = _sentences(new_text)
    comparison = {
        "old_story_B": old_draft_stats(narrative_dir),
        "new_novel": {"chars": len(new_text),
                      "sentences": len(sents),
                      "duplicate_rate": round(
                          1 - len(set(sents)) /
                          max(1, len(sents)), 3),
                      "paragraphs": sum(
                          len(ch["paragraphs"])
                          for ch in novel["chapters"]),
                      "f_levels": {}}}
    for ch in novel["chapters"]:
        for p in ch["paragraphs"]:
            lv = p.get("level", "?")
            comparison["new_novel"]["f_levels"][lv] = \
                comparison["new_novel"]["f_levels"].get(lv, 0) + 1
    return {"report": report.model_dump(mode="json"),
            "comparison": comparison, "review_usage": usage}


def write_quality(run_dir: Path, narrative_dir: Path,
                  out_dir: Path, generate_fn=None,
                  with_review: bool = True) -> dict:
    novel = _load(out_dir / "trace_v2.json")
    result = evaluate(run_dir, narrative_dir, novel,
                      generate_fn=generate_fn,
                      with_review=with_review)
    (out_dir / "narrative_quality_report.json").write_text(
        json.dumps(result["report"], ensure_ascii=False,
                   indent=1), encoding="utf-8")
    (out_dir / "comparison.json").write_text(json.dumps(
        result["comparison"], ensure_ascii=False, indent=1),
        encoding="utf-8")
    return result
