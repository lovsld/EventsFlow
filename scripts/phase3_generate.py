"""Phase 3 Step 4b: regenerate the three plans with the
Phase 3 enrichment (voice / directives / density), generate
the Phase 3 novels, plus one repeated tavern generation for
variance. Post-processes each (quality incl. the separate
voice_differentiation metric, f-budget, scene verification).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from narrative.fbudget import analyze
from narrative.planner import write_plan
from narrative.quality import write_quality
from narrative.scene_verification import write_verification
from narrative.writer2 import write_novel_files

ROOT = Path(__file__).resolve().parent.parent
JOBS = [("runs_v02/rule_v02c4_seed42",
         "narrative_runs/rule_v02c4_seed42",
         "stories/tavern_secret.json", "phase3"),
        ("runs_v02/c5_lighthouse_seed7",
         "narrative_runs/c5_lighthouse_seed7",
         "stories/lighthouse_watch.json", "phase3"),
        ("runs_v02/clinic_v02cfix_seed42",
         "narrative_runs/clinic_v02cfix_seed42",
         "stories/clinic_fever.json", "phase3"),
        ("runs_v02/rule_v02c4_seed42",
         "narrative_runs/rule_v02c4_seed42",
         "stories/tavern_secret.json", "phase3_repeat")]


def process(run_dir, ndir, out):
    novel = write_novel_files(run_dir, ndir, out_dir=out)
    res = write_quality(run_dir, ndir, out)
    fb = analyze(novel)
    (out / "f_budget.json").write_text(
        json.dumps(fb, ensure_ascii=False, indent=1),
        encoding="utf-8")
    ver = json.loads(write_verification(
        ndir, novel, out_dir=out).read_text(encoding="utf-8"))
    rep = res["report"]
    print(out, "| writer:", novel["usage"],
          "| mean:", round(sum(rep["scores"].values())
                           / len(rep["scores"]), 2),
          "| dialogue:", rep["scores"].get(
              "dialogue_naturalness"),
          "| voice:", res["extra_metrics"].get(
              "voice_differentiation"),
          "| factual:", rep["scores"].get(
              "factual_consistency"),
          "| fb:", fb["book"]["ratios"],
          "| ver:", ver["summary"],
          "| review usage:", res["review_usage"])


def main():
    for run, nd, cfg, out_name in JOBS:
        run_dir, ndir = ROOT / run, ROOT / nd
        if out_name == "phase3":
            write_plan(run_dir, ndir, ROOT / cfg,
                       ndir / "upgrade")
        process(run_dir, ndir, ndir / out_name)


if __name__ == "__main__":
    main()
