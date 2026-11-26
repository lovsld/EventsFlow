"""Phase 1 task 2: material adapter — log strings must never
reach the Writer as quotable text."""
from __future__ import annotations

import json
from pathlib import Path

from narrative.material import (ACTION, DIALOGUE, adapt_event,
                                classify, detect_log_leakage,
                                writer_materials)

ROOT = Path(__file__).resolve().parent.parent
NAMES = {"doctor_shen": "沈医生", "nurse_lin": "林护士",
         "porter_wu": "吴大勇"}


def _load(run: str):
    ndir = ROOT / "narrative_runs" / run
    obs = json.loads((ndir / "observations.json")
                     .read_text(encoding="utf-8"))
    nes = json.loads((ndir / "upgrade" / "narrative_events.json")
                     .read_text(encoding="utf-8"))
    return obs, nes


def test_clinic_log_events_become_unquotable_actions():
    obs, nes = _load("clinic_v02cfix_seed42")
    obs_by_id = {o["obs_id"]: o for o in obs}
    ne_by_id = {e["narrative_event_id"]: e for e in nes}
    # E0002: a search (found the admission form) — the event whose
    # template summary "search @ oncall" leaked into the novel.
    m = adapt_event(ne_by_id["NE-E0002"], obs_by_id["E0002"], NAMES)
    assert m["material_class"] == ACTION
    assert m["quotable"] is False
    assert "@" not in m["text"]
    assert "admission_form" in m["text"]  # the find is preserved
    # E0030: speak_failed — an attempt, not a line of dialogue.
    m = adapt_event(ne_by_id["NE-E0030"], obs_by_id["E0030"], NAMES)
    assert m["material_class"] == ACTION
    assert m["quotable"] is False
    assert "@" not in m["text"]


def test_dialogue_stays_verbatim_and_quotable():
    obs, nes = _load("clinic_v02cfix_seed42")
    obs_by_id = {o["obs_id"]: o for o in obs}
    speech = [o for o in obs if o["type"] in ("speak", "ask")
              and o.get("text")]
    assert speech
    ne_by_id = {e["narrative_event_id"]: e for e in nes}
    for o in speech[:5]:
        m = adapt_event(ne_by_id[f"NE-{o['obs_id']}"], o, NAMES)
        assert m["material_class"] == DIALOGUE
        assert m["quotable"] is True
        assert m["text"] == o["text"].strip()


def test_no_quotable_material_matches_log_pattern():
    import re
    log_re = re.compile(r"^[a-z][a-z_]+ @ [a-z_0-9]+$")
    for run in ("rule_v02c4_seed42", "c5_lighthouse_seed7",
                "clinic_v02cfix_seed42"):
        obs, nes = _load(run)
        obs_by_id = {o["obs_id"]: o for o in obs}
        mats = writer_materials(nes, obs_by_id, {})
        for m in mats:
            assert not log_re.match(m["text"]), (run, m)
            if m["quotable"]:
                assert m["material_class"] == DIALOGUE


def test_leakage_detector_catches_baseline_clinic_novel():
    ndir = ROOT / "narrative_runs" / "clinic_v02cfix_seed42"
    trace = json.loads((ndir / "upgrade" / "trace_v2.json")
                       .read_text(encoding="utf-8"))
    paras = [p for ch in trace["chapters"]
             for p in ch["paragraphs"]]
    obs = json.loads((ndir / "observations.json")
                     .read_text(encoding="utf-8"))
    leaks = detect_log_leakage(paras, obs)
    spans = {leak["span"] for leak in leaks}
    assert "search @ oncall" in spans
    assert "speak_failed @ triage" in spans
    clean = [{"text": "沈医生在值班室搜寻，找到了入院表。",
              "chapter_id": "CH01", "event_ids": ["E0002"]}]
    assert detect_log_leakage(clean, obs) == []


def test_classify_unknown_type_is_quarantined():
    assert classify({"type": "debug_dump", "text": "x"}) == "SYSTEM"
