#!/usr/bin/env python3
"""ACP Story Simulator V0.2 entry point (task book section 13).

Usage:
  python main.py --mode llm  --ticks 20 --seed 42   # Gemini via connector (ACP_LLM_PROVIDER=openai + OPENAI_API_KEY for OpenAI)
  python main.py --mode rule --ticks 20 --seed 42   # V0.1 rule control group
  python main.py --mode mock --ticks 20 --seed 42   # deterministic test mode

Local runtime trio (see LOCAL_DEPLOY.md):
  python main.py --mode rule --ticks 20 --run-name night1
  python main.py --resume night1 --ticks 10        # continue an interrupted run from its checkpoint
  python main.py --mode llm --ticks 60 --loop      # quota-aware loop: pause on pool exhaustion instead of burning ticks
"""
from __future__ import annotations

import argparse
import json
import sys

from v02.runner import RUNS_DIR, run


def main() -> int:
    ap = argparse.ArgumentParser(description="ACP Story Simulator V0.2")
    ap.add_argument("--mode", choices=["llm", "rule", "mock"], default=None,
                    help="default: rule (with --resume: the run's own mode)")
    ap.add_argument("--ticks", type=int, default=20,
                    help="ticks to run (with --resume/--loop: additional / total ticks of this invocation)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--resume", default=None, metavar="RUN_NAME",
                    help="resume the named run from its per-tick checkpoint, "
                         "appending --ticks more ticks to the same run directory and database")
    ap.add_argument("--loop", action="store_true",
                    help="quota-aware continuous loop: when the whole Gemini "
                         "key pool is exhausted, save the checkpoint, wait "
                         "ACP_QUOTA_SLEEP_MINUTES and retry the same tick "
                         "(total wait capped by ACP_QUOTA_MAX_WAIT_HOURS)")
    args = ap.parse_args()
    try:
        if args.resume:
            mode = args.mode
            if mode is None:
                meta_path = RUNS_DIR / args.resume / "run_meta.json"
                if not meta_path.exists():
                    print(f"error: cannot resume {args.resume!r}: "
                          f"{meta_path} not found", file=sys.stderr)
                    return 2
                mode = json.loads(meta_path.read_text(encoding="utf-8"))["mode"]
            run_dir = run(mode=mode, ticks=args.ticks, seed=args.seed,
                          resume_from=args.resume, loop=args.loop)
        else:
            run_dir = run(mode=args.mode or "rule", ticks=args.ticks,
                          seed=args.seed, run_name=args.run_name,
                          loop=args.loop)
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"run complete: {run_dir}")
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("quota_stopped"):
            print("note: stopped early — Gemini quota wait budget exhausted; "
                  "the blocked tick was NOT committed. Resume later with: "
                  f"python main.py --resume {run_dir.name} --ticks <more>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
