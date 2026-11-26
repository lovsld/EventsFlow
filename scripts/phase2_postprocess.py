"""Phase 2 tavern novel post-processing: quality (via
quality.write_quality, the canonical entry point), F-budget
analysis, and scene verification, all into phase2/."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from narrative.fbudget import analyze
from narrative.quality import write_quality
from narrative.scene_verification import write_verification

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "runs_v02" / "rule_v02c4_seed42"
ND = ROOT / "narrative_runs" / "rule_v02c4_seed42"
OUT = ND / "phase2"


def main():
    res = write_quality(RUN, ND, OUT)
    rep = res["report"]
    novel = json.loads((OUT / "trace_v2.json")
                       .read_text(encoding="utf-8"))
    fb = analyze(novel)
    (OUT / "f_budget.json").write_text(
        json.dumps(fb, ensure_ascii=False, indent=1),
        encoding="utf-8")
    ver = json.loads(write_verification(ND, novel, out_dir=OUT)
                     .read_text(encoding="utf-8"))
    dims = {d["key"]: d["score"] for d in rep["dimensions"]}
    print("PHASE2 tavern quality mean:", rep["mean_score"])
    for k in ("factual_consistency", "dialogue_naturalness",
              "psychological_plausibility",
              "character_consistency", "knowledge_consistency",
              "narrative_coherence"):
        print(" ", k, "=", dims.get(k))
    print("fbudget book:", {k: round(v, 3) for k, v in
                            fb["book"].items()
                            if k != "total_paras"},
          "| issues:", fb["issues"])
    print("verification:", ver["summary"])
    old_q = json.loads((ND / "phase1" / "quality_v2.json")
                       .read_text(encoding="utf-8"))
    old = {d["key"]: d["score"] for d in old_q["dimensions"]}
    print("PHASE1 v2 mean:", old_q["mean_score"],
          "| factual:", old["factual_consistency"],
          "| dialogue:", old["dialogue_naturalness"],
          "| psych:", old.get("psychological_plausibility"))
    old_fb = json.loads((ND / "phase1" / "f_budget_v2.json")
                        .read_text(encoding="utf-8"))
    print("PHASE1 v2 fbudget:",
          {k: round(v, 3) for k, v in old_fb["book"].items()
           if k != "total_paras"})


if __name__ == "__main__":
    main()
