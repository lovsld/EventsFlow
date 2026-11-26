"""Phase 3 counterfactuals (task book 6.4):
Pair 1 (voice): tavern SC02 generated with the soldier's real
voice profile, then with a mutated profile (observed tendency
replaced by '以询问试探为主', directness traits flipped) --
same events, same state, only the voice block changes.
Pair 2 (relationship): the same scene with the soldier's
trust hint toward the owner flipped from low (0.3, 试探保留)
to high (0.85, 表达直接) -- expression toward the same
addressee should change while stance/facts stay fixed.
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from narrative.gemini_client import generate
from narrative.writer2 import SYSTEM, assemble_context, _prompt

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "runs_v02" / "rule_v02c4_seed42"
ND = ROOT / "narrative_runs" / "rule_v02c4_seed42"
SCENE = "SC02"


def _products():
    return {k: json.loads(p.read_text(encoding="utf-8"))
            for k, p in {
                "events": ND / "upgrade" / "narrative_events.json",
                "observations": ND / "observations.json",
                "semantic": ND / "upgrade" / "semantic.json",
                "arcs": ND / "upgrade" / "character_arcs.json",
                "views": ND / "upgrade" / "story_views.json",
                "setup": ND / "upgrade" / "setup_payoff.json"}.items()}


def _gen(plan):
    scene = next(s for s in plan["scenes"]
                 if s["scene_id"] == SCENE)
    ctx = assemble_context(RUN, ND, scene, plan, _products(),
                           [])["context"]
    text, usage = generate(SYSTEM, _prompt(ctx), max_tokens=4096)
    return text, usage


def main():
    plan = json.loads((ND / "upgrade" / "plan.json")
                      .read_text(encoding="utf-8"))
    base_text, u0 = _gen(plan)

    plan_v = copy.deepcopy(plan)
    part = next(s for s in plan_v["scenes"]
                if s["scene_id"] == SCENE)[
        "character_state"]["participants"]["soldier"]
    part["voice"]["observed_tendencies"] = [
        {"trait": "以询问试探为主", "basis": "counterfactual "
                                            "mutation"}]
    part["voice"]["config_traits"] = {
        "caution": 0.9, "curiosity": 0.7, "honesty": 0.4,
        "impulsiveness": 0.2, "sociability": 0.55}
    voice_text, u1 = _gen(plan_v)

    plan_r = copy.deepcopy(plan)
    part_r = next(s for s in plan_r["scenes"]
                  if s["scene_id"] == SCENE)[
        "character_state"]["participants"]["soldier"]
    for r in part_r["voice"]["relationship_expression"]:
        if r["with"] == "owner":
            r["trust"] = 0.85
            r["hint"] = "高信任：表达直接"
    rel_text, u2 = _gen(plan_r)

    out = ND / "phase3" / "counterfactual"
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps({
        "scene_id": SCENE,
        "base": base_text, "voice_mutated": voice_text,
        "relationship_mutated": rel_text,
        "usage": {"base": u0, "voice": u1, "rel": u2}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("phase3 counterfactuals done")


if __name__ == "__main__":
    main()
