#!/usr/bin/env python3
"""ACP Story Simulator V0.2 entry point (task book section 13).

Usage:
  python main.py --mode llm  --ticks 20 --seed 42   # Gemini via connector (ACP_LLM_PROVIDER=openai + OPENAI_API_KEY for OpenAI)
  python main.py --mode rule --ticks 20 --seed 42   # V0.1 rule control group
  python main.py --mode mock --ticks 20 --seed 42   # deterministic test mode
"""
from __future__ import annotations

import argparse
import sys

from v02.runner import run


def main() -> int:
    ap = argparse.ArgumentParser(description="ACP Story Simulator V0.2")
    ap.add_argument("--mode", choices=["llm", "rule", "mock"], default="rule")
    ap.add_argument("--ticks", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--run-name", default=None)
    args = ap.parse_args()
    try:
        run_dir = run(mode=args.mode, ticks=args.ticks, seed=args.seed,
                      run_name=args.run_name)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"run complete: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
