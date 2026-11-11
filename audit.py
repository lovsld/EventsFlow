#!/usr/bin/env python3
"""Step-1 closing audit entry point (motivation/behaviour consistency).

Usage:
  python audit.py --seed-group runs_v02/exp_seedgroup_rule
  python audit.py --runs runs_v02/rule_phasec_seed42 runs_v02/rule_seed42
  python audit.py --seed-group runs_v02/exp_seedgroup_rule \
      --out runs_v02/step1_audit

Reads FINISHED runs only (acp.sqlite3 + world_log.json + summary.json);
never runs a simulation and never calls a provider/LLM. Metric
definitions live in v02/audit.py's module docstring. Outputs
audit_summary.json (full per-run metrics + aggregate) and
audit_table.md (the comparison table) into --out.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from v02.audit import audit_runs, render_markdown


def main() -> int:
    ap = argparse.ArgumentParser(
        description="ACP Story Simulator — step-1 consistency audit")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--seed-group", default=None,
                     help="directory holding one run subdirectory per seed")
    src.add_argument("--runs", nargs="+", default=None,
                     help="explicit list of run directories")
    ap.add_argument("--out", default="runs_v02/step1_audit",
                    help="output directory (default: runs_v02/step1_audit)")
    args = ap.parse_args()

    if args.seed_group:
        root = Path(args.seed_group)
        run_dirs = sorted(d for d in root.iterdir()
                          if d.is_dir() and (d / "acp.sqlite3").exists())
    else:
        run_dirs = [Path(d) for d in args.runs]
    if not run_dirs:
        print("no run directories found", file=sys.stderr)
        return 1

    report = audit_runs(run_dirs)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "audit_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "audit_table.md").write_text(render_markdown(report), encoding="utf-8")
    agg = report["aggregate"]
    print(f"audited {agg['runs']} runs / {agg['decisions']} decisions -> {out}")
    print(f"M1a {agg['m1a']['rate']}  M1b {agg['m1b']['rate']}  "
          f"M2 {agg['m2']['rate']}  M4 rejection {agg['m4']['rate']}  "
          f"M6 {agg['m6']['rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
