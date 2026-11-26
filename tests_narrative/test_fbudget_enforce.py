"""Phase 2 acceptance (task book section 2.2): the F budget
must actually constrain generation, not only measure it.
_enforce_budget is tested with stub generators: conversion,
rejection safety, and explicit (never silent) diagnosis."""
import json

from narrative.writer2 import _enforce_budget


def _novel(f3=7, f1=2, f2=1):
    paras = []
    for i in range(f1):
        paras.append({"text": f"事实段{i}", "level": "F1",
                      "event_ids": [f"E{i:04d}"],
                      "scene_id": "S1", "chapter_id": "CH01"})
    for i in range(f2):
        paras.append({"text": f"呈现段{i}", "level": "F2",
                      "event_ids": [], "scene_id": "S1",
                      "chapter_id": "CH01"})
    for i in range(f3):
        paras.append({"text": f"补充段{i}：纯解释与描写",
                      "level": "F3", "event_ids": [f"E{100+i:04d}"],
                      "scene_id": "S1", "chapter_id": "CH01"})
    return {"chapters": [{"chapter_id": "CH01", "title": "第1章",
                          "paragraphs": paras}],
            "usage": {"prompt_tokens": 0, "candidates_tokens": 0,
                      "calls": 0},
            "creative_register": [], "stopped": None,
            "plan_validation": {}}


def _usage():
    return {"prompt_tokens": 0, "candidates_tokens": 0, "calls": 0}


def _good_gen(system, prompt, max_tokens=8192):
    payload = json.loads(prompt)
    out = [{"text": f"改写呈现{n['n']}：以动作带出信息",
            "level": "F2", "event_ids": n["event_ids"]}
           for n in payload["paragraphs"]]
    return json.dumps({"paragraphs": out}, ensure_ascii=False), \
        {"prompt_tokens": 100, "candidates_tokens": 50}


def test_over_budget_novel_is_converted_and_traceable():
    novel = _novel(f3=4, f1=4, f2=2)
    rec = _enforce_budget(novel, _good_gen, _usage())
    assert rec["before"]["F3"] == 0.4
    assert rec["status"] == "within_budget_after_rewrite"
    assert rec["after"]["F3"] <= 0.2
    assert rec["rewritten_paragraphs"] == 2     # only what is needed
    paras = novel["chapters"][0]["paragraphs"]
    assert len(paras) == 10                     # nothing deleted
    f1 = [p for p in paras if p["level"] == "F1"]
    assert len(f1) == 4                         # F1 untouched
    ids = sorted(e for p in paras for e in p["event_ids"])
    assert len(ids) == 8                        # traceability kept


def test_bad_rewrite_is_rejected_and_diagnosis_is_explicit():
    def bad_gen(system, prompt, max_tokens=8192):
        return "not json at all", {"prompt_tokens": 10,
                                   "candidates_tokens": 5}
    novel = _novel()
    rec = _enforce_budget(novel, bad_gen, _usage())
    assert rec["status"] == "over_budget_after_rewrite"
    assert "F3_OVER_MAX" in rec["issues_after"]  # never silent
    assert rec["rewritten_paragraphs"] == 0
    assert novel["chapters"][0]["paragraphs"][3]["level"] == "F3"


def test_within_budget_novel_triggers_no_calls():
    calls = []

    def counting_gen(system, prompt, max_tokens=8192):
        calls.append(1)
        return "{}", {"prompt_tokens": 1, "candidates_tokens": 1}
    novel = _novel(f3=1, f1=5, f2=4)
    rec = _enforce_budget(novel, counting_gen, _usage())
    assert rec["status"] == "within_budget"
    assert calls == []


def _novel_from_counts(f1, f2, f3):
    paras = ([{"level": "F1", "text": "事实。", "event_ids": ["E1"]}
              ] * f1
             + [{"level": "F2", "text": "呈现。", "event_ids": []}
                ] * f2
             + [{"level": "F3", "text": "补充。", "event_ids": []}
                ] * f3)
    return {"chapters": [{"chapter_id": "CH01",
                          "paragraphs": paras}],
            "usage": {"prompt_tokens": 0, "candidates_tokens": 0}}


def test_f3_soft_target_is_not_over_budget():
    from narrative.fbudget import analyze
    rep = analyze(_novel_from_counts(10, 10, 0))   # F3 = 0.0
    assert rep["over_budget"] is False
    assert rep["soft_issues"] and \
        rep["soft_issues"][0]["kind"] == "F3_BELOW_TARGET"


def test_enforcement_records_waiver_when_f2_floor_wins():
    # 20 paras: F2 needs 6 conversions, only 5 F3 sit above
    # the 0.05 soft target -> convert 6, F3 lands at 0.0, and
    # the waiver must be recorded with its reason.
    novel = _novel_from_counts(12, 2, 6)
    calls = []

    def fake_gen(system, prompt, max_tokens=4096):
        import json as _json
        payload = _json.loads(prompt)
        calls.append(len(payload["paragraphs"]))
        out = {"paragraphs": [
            {"n": p["n"], "level": "F2",
             "text": "改写为呈现。", "event_ids": p["event_ids"]}
            for p in payload["paragraphs"]]}
        return _json.dumps(out, ensure_ascii=False), {
            "prompt_tokens": 10, "candidates_tokens": 10}

    rec = _enforce_budget(novel, fake_gen,
                          {"prompt_tokens": 0,
                           "candidates_tokens": 0, "calls": 0})
    assert rec["f3_target_waived"]["target_min"] == 0.05
    assert rec["after"]["F3"] == 0.0
    assert rec["after"]["F2"] >= 0.40
