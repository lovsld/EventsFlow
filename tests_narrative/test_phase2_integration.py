"""Phase 2 integration (task book sections 4.1-4.7, 5.1-5.2):
the character-state view, incremental updates, planner state
enrichment, writer context, and post-scene verification must
be genuinely connected, with provenance tiers intact."""
import json
from pathlib import Path

from narrative.character_state import (build_character_states,
                                       knowledge_at)
from narrative.planner import build_plan
from narrative.psychology import build_state_updates
from narrative.scene_verification import verify_novel

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "runs_v02" / "rule_v02c4_seed42"
ND = ROOT / "narrative_runs" / "rule_v02c4_seed42"


def test_state_view_tiers_and_coverage():
    data = build_character_states(RUN, ND)
    assert set(data["characters"]) == {"merchant", "owner",
                                       "soldier"}
    for view in data["characters"].values():
        assert view["knowledge"]["tier"] == "fact"
        assert view["emotions"]["tier"] == "inference"
        assert view["motivations"]["tier"] == "hypothesis"
        known = {k["fact"] for k in
                 view["knowledge"]["known_facts"]}
        missing = set(view["knowledge"]["not_known_key_facts"])
        assert not (known & missing)          # partition, no overlap
        assert view["goals"]                  # everyone has goals


def test_knowledge_at_respects_learning_order():
    data = build_character_states(RUN, ND)
    soldier = data["characters"]["soldier"]
    early = knowledge_at(soldier, 3)
    late = knowledge_at(soldier, 99)
    # the letter is found in-scene at tick 3 (event E0008's tick
    # is 3 in the world log; belief change recorded at tick 3)
    assert "speaker_knows_secret" not in early   # learned tick 9
    assert "speaker_knows_secret" in late
    assert set(early) <= set(late)               # monotonic


def test_state_updates_have_evidence_and_alternatives():
    data = build_state_updates(ND)
    assert data["updates"]
    for u in data["updates"]:
        assert u["event_id"] and u["character_id"]
        assert "before" in u and "after" in u
        if u["tier"] == "fact":
            assert u["confidence"] == 1.0
            assert u["evidence_event_ids"]
        else:
            # inference / hypothesis never travel without an
            # alternative explanation or cited evidence
            assert u["alternative_explanations"] or \
                u["evidence_event_ids"]


def test_planner_scenes_carry_state_and_knowledge_discipline():
    plan = build_plan(RUN, ND, ROOT / "stories" /
                      "tavern_secret.json")
    assert plan["validation"]["errors"] == []
    sc01 = plan["scenes"][0]
    cs = sc01["character_state"]
    soldier = cs["participants"]["soldier"]
    # at SC01 entry the soldier has NOT found the letter yet:
    # it must be in must_not_use, and learning it must be an
    # expected change of this very scene
    assert "anonymous_letter_exists" in \
        soldier["must_not_use_at_entry"]
    assert "anonymous_letter_exists" not in \
        soldier["knows_at_entry"]
    exp_facts = {e["after"].get("fact")
                 for e in cs["expected_state_changes"]
                 if e["field"] == "knowledge"}
    assert "anonymous_letter_exists" in exp_facts
    assert cs["information_gaps"]  # the tavern runs on secrets


def test_writer_context_and_prompt_carry_state():
    from narrative.writer2 import assemble_context, _prompt
    plan = json.loads((ND / "upgrade" / "plan.json")
                      .read_text(encoding="utf-8"))
    products = {
        k: json.loads(p.read_text(encoding="utf-8"))
        for k, p in {
            "events": ND / "upgrade" / "narrative_events.json",
            "observations": ND / "observations.json",
            "semantic": ND / "upgrade" / "semantic.json",
            "arcs": ND / "upgrade" / "character_arcs.json",
            "views": ND / "upgrade" / "story_views.json",
            "setup": ND / "upgrade" / "setup_payoff.json"}.items()}
    scene = plan["scenes"][0]
    ctx = assemble_context(RUN, ND, scene, plan, products,
                           [])["context"]
    assert ctx["character_state"] is not None
    text = _prompt(ctx)
    assert "人物状态与本场约束" in text
    assert "说明书式旁白" in text          # show, don't tell
    assert "不得当作已知使用" in text      # knowledge discipline


def test_verification_records_diffs_without_touching_text():
    novel = json.loads((ND / "phase1" / "trace_v2.json")
                       .read_text(encoding="utf-8"))
    before = json.dumps(novel, ensure_ascii=False)
    rep = verify_novel(ND, novel)
    assert rep["summary"]["scenes"] == 5
    assert rep["summary"]["by_kind"][
        "future_event_reference"] == 0
    assert json.dumps(novel, ensure_ascii=False) == before
