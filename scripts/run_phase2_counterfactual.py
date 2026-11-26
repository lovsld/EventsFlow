"""Phase 2 counterfactual (task book 5.4): the SAME scene of
the SAME event stream is generated twice -- once with the
character's real state at scene entry, once with a mutated
state (the soldier already knows the embezzlement and the
letter at SC01 entry, and enters angry instead of calm).
Only the character_state block of the plan changes; events,
outline, and every other context input are identical. If the
state truly participates in generation, the two texts must
differ in behaviour, not just wording.
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


def _products():
    return {k: json.loads(p.read_text(encoding="utf-8"))
            for k, p in {
                "events": ND / "upgrade" / "narrative_events.json",
                "observations": ND / "observations.json",
                "semantic": ND / "upgrade" / "semantic.json",
                "arcs": ND / "upgrade" / "character_arcs.json",
                "views": ND / "upgrade" / "story_views.json",
                "setup": ND / "upgrade" / "setup_payoff.json"}.items()}


def _gen(plan, scene_id):
    scene = next(s for s in plan["scenes"]
                 if s["scene_id"] == scene_id)
    ctx = assemble_context(RUN, ND, scene, plan, _products(),
                           [])["context"]
    prompt = _prompt(ctx)
    text, usage = generate(SYSTEM, prompt, max_tokens=4096)
    return prompt, text, usage


def main():
    plan = json.loads((ND / "upgrade" / "plan.json")
                      .read_text(encoding="utf-8"))
    prompt_a, text_a, u_a = _gen(plan, "SC01")

    plan_b = copy.deepcopy(plan)
    scene_b = next(s for s in plan_b["scenes"]
                   if s["scene_id"] == "SC01")
    part = scene_b["character_state"]["participants"]["soldier"]
    moved = ["anonymous_letter_exists", "mayor_embezzled"]
    part["knows_at_entry"] = sorted(
        set(part["knows_at_entry"]) | set(moved))
    part["must_not_use_at_entry"] = [
        f for f in part["must_not_use_at_entry"] if f not in moved]
    part["emotion_entering"] = {
        "emotion": "anger", "intensity": 0.8, "confidence": 0.4,
        "note": "counterfactual mutation for this test"}
    prompt_b, text_b, u_b = _gen(plan_b, "SC01")

    out = ND / "phase2" / "counterfactual"
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps({
        "scene_id": "SC01",
        "mutation": {"soldier.knows_at_entry +=": moved,
                     "soldier.emotion_entering": "anger"},
        "prompt_differs_only_in_state_block":
            prompt_a.replace("calm", "") != prompt_b,
        "usage_a": u_a, "usage_b": u_b,
        "text_a": text_a, "text_b": text_b},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("counterfactual done:",
          {k: (u_a.get(k), u_b.get(k))
           for k in ("input_tokens", "output_tokens")})


if __name__ == "__main__":
    main()
