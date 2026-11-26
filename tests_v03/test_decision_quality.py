"""Phase 1 task 1: the progress judge must flag the Phase 6 LLM
runs' zero mainline engagement as a diagnosis (not normal
operation), and must pass the rule runs clean."""
from __future__ import annotations

from pathlib import Path

from v03.decision_quality import judge_run

ROOT = Path(__file__).resolve().parent.parent
SCEN = ROOT / "scenarios" / "tavern_letter.json"
CFG = ROOT / "stories" / "tavern_secret.json"


def _judge(name: str) -> dict:
    return judge_run(ROOT / "v03_runs" / "phase6" / name, SCEN, CFG)


def test_llm_run_is_diagnosed_not_normal():
    r = _judge("llm_on_seed42")
    s = r["summary"]
    assert s["ticks"] == 20
    assert s["mainline_zero_engagement"] is True
    assert s["question_progress_ticks"] == 0
    assert s["final_story_progress"] == 0.0
    assert s["diagnosis_count"] >= 1
    assert all(d["kind"] == "ZERO_PROGRESS_STREAK"
               for d in r["diagnoses"])
    # every tick carries a complete decision record (task §3.5)
    for rec in r["decisions"]:
        assert rec["decision_id"].startswith("D")
        assert rec["outcome_type"] in (
            "new_event", "advance_conflict", "character_choice",
            "reveal_information", "hold_observe",
            "trigger_diagnosis")
        assert rec["rationale"]
    # no question was even addressed, let alone answered
    last = r["snapshots"][-1]
    assert last["answered_questions"] == []
    assert last["addressed_questions"] == []


def test_rule_run_progresses_without_diagnosis():
    r = _judge("rule_on_seed42")
    s = r["summary"]
    assert s["diagnosis_count"] == 0
    assert s["mainline_zero_engagement"] is False
    assert s["progress_ticks"] == 20
    last = r["snapshots"][-1]
    # the letter find answers letter_exists with direct evidence
    assert "letter_exists" in last["answered_questions"]
    # embezzlement was only ever hearsay: addressed, NOT answered
    assert "embezzled" in last["addressed_questions"]
    assert "embezzled" not in last["answered_questions"]
    outcomes = {d["outcome_type"] for d in r["decisions"]}
    assert "reveal_information" in outcomes


def test_every_progress_claim_cites_evidence():
    r = _judge("rule_on_seed42")
    for rec in r["decisions"]:
        for dim, d in rec["dimensions"].items():
            if d["advanced"]:
                assert d["evidence"] or dim == "goal", (
                    rec["decision_id"], dim)
