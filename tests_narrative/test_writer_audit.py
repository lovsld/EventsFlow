"""N3 fidelity-audit tests: a clean synthetic draft passes; each
classic fabrication is caught. No real LLM calls (mock chat)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from narrative.audit import audit_draft  # noqa: E402
from narrative.writer import write_form  # noqa: E402

OBS = [
    {"obs_id": "E0001", "tick": 1, "type": "speak", "narrative_role": "plot",
     "text": "甲：天快黑了。"},
    {"obs_id": "E0002", "tick": 2, "type": "search",
     "narrative_role": "plot", "text": None},
    {"obs_id": "E0003", "tick": 3, "type": "ambient",
     "narrative_role": "texture", "text": None},
]
LINES = {"lines": [{"line_id": "L01", "event_ids": ["E0001", "E0002"]}],
         "fragments": []}


def draft(paras, form="B", omitted=None):
    return {"title": "t", "paragraphs": paras,
            "omitted": omitted or [],
            "meta": {"form": form}}


def para(pid, text, ids, mode="paraphrase"):
    return {"id": pid, "text": text, "event_ids": ids, "mode": mode}


GOOD = [para("P01", "甲说：“甲：天快黑了。”", ["E0001"], "quote"),
        para("P02", "随后他去搜查了柜子。", ["E0002"])]


def test_clean_draft_passes():
    r = audit_draft(draft(GOOD), OBS, LINES)
    assert r["pass"] and r["quotes_verbatim"] == r["quotes_checked"] == 1


def test_tampered_quote_fails():
    bad = [para("P01", "甲说：“甲：天已经黑透了。”", ["E0001"], "quote"),
           GOOD[1]]
    r = audit_draft(draft(bad), OBS, LINES)
    assert not r["pass"] and any("verbatim" in v for v in r["violations"])


def test_unmapped_fact_paragraph_fails():
    bad = GOOD + [para("P03", "其实他心里非常害怕。", [], "paraphrase")]
    r = audit_draft(draft(bad), OBS, LINES)
    assert not r["pass"] and r["unsupported_fact_paragraphs"] == 1


def test_missing_main_line_event_fails():
    r = audit_draft(draft([GOOD[0]]), OBS, LINES)
    assert not r["pass"] and any("coverage" in v for v in r["violations"])


def test_time_reversal_fails():
    r = audit_draft(draft([GOOD[1], GOOD[0]]), OBS, LINES)
    assert not r["pass"] and any("temporal" in v for v in r["violations"])


def test_omitting_line_event_fails_but_texture_ok():
    bad = draft([GOOD[0]], omitted=[{"event_id": "E0002",
                                     "reason": "重复"}])
    r = audit_draft(bad, OBS, LINES)
    assert not r["pass"]


def test_form_a_requires_full_plot_coverage():
    r = audit_draft(draft([GOOD[0]], form="A"), OBS, LINES)
    assert not r["pass"]


def test_writer_with_mock_chat(tmp_path):
    run = "rule_v02c4_seed42"
    narrative_dir = PROJECT / "narrative_runs" / run
    payload = {"title": "酒馆", "paragraphs": [], "omitted": []}
    obs = json.loads((narrative_dir / "observations.json")
                     .read_text(encoding="utf-8"))
    storylines = json.loads((narrative_dir / "storylines.json")
                            .read_text(encoding="utf-8"))
    members = [e for line in storylines["lines"]
               for e in line["event_ids"]]
    plot = [o["obs_id"] for o in obs
            if o["narrative_role"] in ("plot", "structure")]
    need = list(dict.fromkeys(members + plot))
    by_id = {o["obs_id"]: o for o in obs}
    order = [o["obs_id"] for o in obs]
    need.sort(key=order.index)
    for eid in need:
        text = by_id[eid]["text"]
        payload["paragraphs"].append(
            {"text": f"他说：“{text}”" if text else "事情发生了。",
             "event_ids": [eid],
             "mode": "quote" if text else "paraphrase"})

    def fake_chat(messages, **kw):
        return json.dumps(payload, ensure_ascii=False), \
            {"prompt_tokens": 10, "completion_tokens": 5}

    out = tmp_path / "n"
    out.mkdir()
    import shutil
    for name in ("observations.json", "storylines.json", "scenes.json",
                 "frontier.json"):
        shutil.copy(narrative_dir / name, out / name)
    result = write_form(PROJECT / "runs_v02" / run, out, "B",
                        chat_fn=fake_chat)
    assert result["pass"], result["violations"][:3]
    assert (out / "story_B.md").exists()
    assert (out / "trace_B.json").exists()
