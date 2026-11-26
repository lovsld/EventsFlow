"""Phase 3 (task book Step 2): voice section, repetition
directives, density, writer prompt rules, F3 redundancy
ordering, and the separate voice_differentiation metric."""
import json
from pathlib import Path

from narrative.character_state import build_character_states
from narrative.planner import build_plan

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "runs_v02" / "rule_v02c4_seed42"
ND = ROOT / "narrative_runs" / "rule_v02c4_seed42"
CFG = ROOT / "stories" / "tavern_secret.json"


def test_voice_section_sources_labelled():
    data = build_character_states(RUN, ND)
    soldier = data["characters"]["soldier"]["voice"]
    assert soldier["observed_samples"] >= 5
    assert soldier["observed_tendencies"]      # >= 5 samples
    assert soldier["sources"]["config_traits"] == \
        "character_config"
    assert soldier["config_traits"]["honesty"] == 0.9
    assert any(r["with"] == "merchant"
               for r in soldier["relationship_expression"])


def test_directives_balance_and_density():
    plan = build_plan(RUN, ND, CFG)
    keeps = compresses = 0
    for s in plan["scenes"]:
        assert s["density"]["grade"] in ("high", "mid", "low")
        for v in s["repetition_directives"].values():
            if v["action"] == "keep":
                keeps += 1
            else:
                compresses += 1
    # the tavern stream is mostly repeats: compress must be
    # the majority, but first presentations survive
    assert compresses > keeps >= 4


def test_writer_prompt_carries_voice_and_rules():
    from narrative.writer2 import assemble_context, _prompt
    plan = build_plan(RUN, ND, CFG)
    products = {
        k: json.loads(p.read_text(encoding="utf-8"))
        for k, p in {
            "events": ND / "upgrade" / "narrative_events.json",
            "observations": ND / "observations.json",
            "semantic": ND / "upgrade" / "semantic.json",
            "arcs": ND / "upgrade" / "character_arcs.json",
            "views": ND / "upgrade" / "story_views.json",
            "setup": ND / "upgrade" / "setup_payoff.json"}.items()}
    scene = next(s for s in plan["scenes"]
                 if s["repetition_directives"])
    ctx = assemble_context(RUN, ND, scene, plan, products,
                           [])["context"]
    text = _prompt(ctx)
    assert "表达倾向" in text
    assert "处置=compress" in text
    assert "剧本格式" in text            # no 人名： labels
    assert "复述一遍" in text            # no emotion restatement
    assert "节奏" in text                # density guidance


def test_verification_new_kinds_present():
    from narrative.scene_verification import verify_novel
    novel = json.loads((ND / "phase2" / "trace_v2.json")
                       .read_text(encoding="utf-8"))
    rep = verify_novel(ND, novel)
    assert "verbatim_dialogue_repeat" in rep["summary"]["by_kind"]
    assert "emotion_restatement" in rep["summary"]["by_kind"]


def test_voice_metric_kept_out_of_scores():
    from narrative.quality import evaluate

    def fake_gen(system, prompt, max_tokens=2048,
                 temperature=0.3):
        dims = ["causal_coherence", "character_consistency",
                "arc_plausibility", "emotional_variation",
                "dialogue_naturalness", "readability",
                "voice_differentiation"]
        out = {"scores": {d: 4 for d in dims}, "issues": []}
        return json.dumps(out), {"prompt_tokens": 1,
                                 "candidates_tokens": 1}
    novel = json.loads((ND / "phase2" / "trace_v2.json")
                       .read_text(encoding="utf-8"))
    res = evaluate(RUN, ND, novel, generate_fn=fake_gen)
    assert "voice_differentiation" not in \
        res["report"]["scores"]
    assert res["extra_metrics"]["voice_differentiation"] == 4
