"""Resume Phase 3 generation after the 2026-11-26 runtime
restart: tavern phase3 is complete; lighthouse has its novel
but no post-processing; clinic and the tavern repeat have
nothing. Idempotent -- completed pieces are not redone."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase3_generate import process
from narrative.fbudget import analyze
from narrative.planner import write_plan
from narrative.quality import write_quality
from narrative.scene_verification import write_verification

ROOT = Path(__file__).resolve().parent.parent


def postprocess(run_dir, ndir, out):
    novel = json.loads((out / "trace_v2.json")
                       .read_text(encoding="utf-8"))
    res = write_quality(run_dir, ndir, out)
    fb = analyze(novel)
    (out / "f_budget.json").write_text(
        json.dumps(fb, ensure_ascii=False, indent=1),
        encoding="utf-8")
    ver = json.loads(write_verification(
        ndir, novel, out_dir=out).read_text(encoding="utf-8"))
    rep = res["report"]
    print(out, "| mean:", round(sum(rep["scores"].values())
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
    lh_run = ROOT / "runs_v02/c5_lighthouse_seed7"
    lh_nd = ROOT / "narrative_runs/c5_lighthouse_seed7"
    postprocess(lh_run, lh_nd, lh_nd / "phase3")
    cl_run = ROOT / "runs_v02/clinic_v02cfix_seed42"
    cl_nd = ROOT / "narrative_runs/clinic_v02cfix_seed42"
    write_plan(cl_run, cl_nd, ROOT / "stories/clinic_fever.json",
               cl_nd / "upgrade")
    process(cl_run, cl_nd, cl_nd / "phase3")
    tv_run = ROOT / "runs_v02/rule_v02c4_seed42"
    tv_nd = ROOT / "narrative_runs/rule_v02c4_seed42"
    process(tv_run, tv_nd, tv_nd / "phase3_repeat")


if __name__ == "__main__":
    main()
