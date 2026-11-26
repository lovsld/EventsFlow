"""Phase 1 task 3: scene records + information-increment checks."""
from __future__ import annotations

import json
from pathlib import Path

from narrative.scene_records import build_scene_records
from narrative.writer2 import assemble_context

ROOT = Path(__file__).resolve().parent.parent


def _novel(run: str) -> dict:
    return json.loads((ROOT / "narrative_runs" / run / "upgrade"
                       / "trace_v2.json").read_text(
                           encoding="utf-8"))


def test_records_cover_all_scenes_with_increments():
    ndir = ROOT / "narrative_runs" / "rule_v02c4_seed42"
    rec = build_scene_records(ndir, _novel("rule_v02c4_seed42"))
    assert rec["summary"]["scenes"] == 5
    # the letter find is a first reveal in exactly one scene
    find_scenes = [r["scene_id"] for r in rec["scenes"]
                   if "anonymous_letter_exists" in r["new_facts"]]
    assert len(find_scenes) == 1
    for r in rec["scenes"]:
        assert r["narrative_goal"]
        assert r["has_increment"]


def test_valueless_repeat_is_flagged_synthetic():
    ndir = ROOT / "narrative_runs" / "rule_v02c4_seed42"
    novel = _novel("rule_v02c4_seed42")
    # duplicate an F1 paragraph of scene 1 into the last chapter,
    # re-labelled with the last scene's id: same event, same
    # wording, no new fact -> must be flagged.
    src = next(p for ch in novel["chapters"]
               for p in ch["paragraphs"]
               if p.get("event_ids") and len(p["text"]) > 30)
    last_scene = novel["chapters"][-1]["paragraphs"][-1]["scene_id"]
    dup = dict(src, scene_id=last_scene)
    novel["chapters"][-1]["paragraphs"].append(dup)
    rec = build_scene_records(ndir, novel)
    assert rec["summary"]["valueless_repeat_paragraphs"] >= 1
    assert rec["summary"]["scene_repetition_rate"] > 0


def test_writer_context_carries_already_revealed():
    ndir = ROOT / "narrative_runs" / "rule_v02c4_seed42"
    run_dir = ROOT / "runs_v02" / "rule_v02c4_seed42"
    plan = json.loads((ndir / "upgrade" / "plan.json")
                      .read_text(encoding="utf-8"))
    products = {
        "events": json.loads((ndir / "upgrade"
                              / "narrative_events.json")
                             .read_text(encoding="utf-8")),
        "observations": json.loads((ndir / "observations.json")
                                   .read_text(encoding="utf-8")),
        "semantic": json.loads((ndir / "upgrade" / "semantic.json")
                               .read_text(encoding="utf-8")),
        "arcs": json.loads((ndir / "upgrade"
                            / "character_arcs.json")
                           .read_text(encoding="utf-8")),
        "views": json.loads((ndir / "upgrade" / "story_views.json")
                            .read_text(encoding="utf-8")),
        "setup": json.loads((ndir / "upgrade"
                             / "setup_payoff.json")
                            .read_text(encoding="utf-8"))}
    last = plan["scenes"][-1]
    out = assemble_context(run_dir, ndir, last, plan, products, [])
    assert "anonymous_letter_exists" in \
        out["context"]["already_revealed"]
    first = plan["scenes"][0]
    out0 = assemble_context(run_dir, ndir, first, plan, products,
                            [])
    assert out0["context"]["already_revealed"] == []
