"""Phase 3 Step 4a: generate the Phase 2 baselines for the
lighthouse and clinic runs. Their on-disk plans are still the
Phase 2 vintage (no directives / density / voice), and the
writer renders Phase 3 additions only when that plan data is
present -- so these novels are genuine Phase 2 outputs.
Writes novel + quality + f-budget + scene verification into
each run's phase2/ directory."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from narrative.fbudget import analyze
from narrative.quality import write_quality
from narrative.scene_verification import write_verification
from narrative.writer2 import write_novel_files

ROOT = Path(__file__).resolve().parent.parent
JOBS = [("runs_v02/c5_lighthouse_seed7",
         "narrative_runs/c5_lighthouse_seed7"),
        ("runs_v02/clinic_v02cfix_seed42",
         "narrative_runs/clinic_v02cfix_seed42")]


def main():
    for run, nd in JOBS:
        run_dir, ndir = ROOT / run, ROOT / nd
        out = ndir / "phase2"
        novel = write_novel_files(run_dir, ndir, out_dir=out)
        res = write_quality(run_dir, ndir, out)
        fb = analyze(novel)
        (out / "f_budget.json").write_text(
            json.dumps(fb, ensure_ascii=False, indent=1),
            encoding="utf-8")
        ver = json.loads(write_verification(
            ndir, novel, out_dir=out).read_text(encoding="utf-8"))
        rep = res["report"]
        print(run, "| writer:", novel["usage"],
              "| mean:", round(sum(rep["scores"].values())
                               / len(rep["scores"]), 2),
              "| dialogue:", rep["scores"].get(
                  "dialogue_naturalness"),
              "| factual:", rep["scores"].get(
                  "factual_consistency"),
              "| fb:", fb["book"]["ratios"],
              "| ver diffs:", ver["summary"]["total_diffs"],
              "| review usage:", res["review_usage"])


if __name__ == "__main__":
    main()
