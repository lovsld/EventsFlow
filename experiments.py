#!/usr/bin/env python3
"""Phase D experiment entry point (seed groups & counterfactuals).

Usage:
  python experiments.py seed-group --seeds 42,7,101,2024,555 --ticks 20 \
      --mode rule --out runs_v02/exp_seedgroup_rule
  python experiments.py counterfactual --seed 42 --ticks 20 \
      --recipient soldier --actor merchant \
      --claim mayor_late_payment,answer_mayor \
      --out runs_v02/exp_counterfactual

All runs use the deterministic rule/mock stack: no LLM calls, so every
comparison is strictly reproducible. Artefacts (runs, tables,
comparison) land in --out.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from v02.experiments import run_counterfactual, run_seed_group


def main() -> int:
    ap = argparse.ArgumentParser(description="ACP Story Simulator V0.2 — Phase D experiments")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sg = sub.add_parser("seed-group", help="same configuration across a group of seeds")
    sg.add_argument("--seeds", default="42,7,101,2024,555",
                    help="comma-separated seeds (default: 42,7,101,2024,555)")
    sg.add_argument("--ticks", type=int, default=20)
    sg.add_argument("--mode", choices=["rule", "mock"], default="rule")
    sg.add_argument("--out", default=None)

    cf = sub.add_parser("counterfactual",
                        help="control vs one suppressed delivery (same seed)")
    cf.add_argument("--seed", type=int, default=42)
    cf.add_argument("--ticks", type=int, default=20)
    cf.add_argument("--mode", choices=["rule", "mock"], default="rule")
    cf.add_argument("--recipient", default="soldier",
                    help="agent whose delivery is suppressed")
    cf.add_argument("--actor", default=None, help="speaker who must have produced the event")
    cf.add_argument("--claim", default=None,
                    help="comma-separated claim tag(s) identifying the utterance")
    cf.add_argument("--topic", default=None)
    cf.add_argument("--type", default=None, dest="etype",
                    help="event type to match (e.g. speak)")
    cf.add_argument("--out", default=None)

    args = ap.parse_args()
    if args.cmd == "seed-group":
        seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
        out = Path(args.out) if args.out else None
        summary = run_seed_group(mode=args.mode, seeds=seeds, ticks=args.ticks,
                                 out_root=out)
        m = summary["metrics"]
        print(f"seed group complete: {len(summary['runs'])} runs "
              f"({args.mode}, {args.ticks} ticks)")
        print(f"trajectory diversity (mean pairwise Jaccard): "
              f"{m['trajectory_diversity_mean_jaccard']['mean']}")
        c = m["soldier_read_letter_consistency"]
        print(f"soldier read the letter in {c['hits']}/{c['runs']} runs")
        print(f"tables: {(out or 'runs_v02/exp_seedgroup')}/seed_group_table.md")
        return 0
    rule: dict = {"recipient": args.recipient}
    if args.actor:
        rule["actor"] = args.actor
    if args.claim:
        claims = [c.strip() for c in args.claim.split(",") if c.strip()]
        rule["claim"] = claims if len(claims) > 1 else claims[0]
    if args.topic:
        rule["topic"] = args.topic
    if args.etype:
        rule["type"] = args.etype
    out = Path(args.out) if args.out else None
    comp = run_counterfactual(seed=args.seed, ticks=args.ticks, rule=rule,
                              out_root=out, mode=args.mode)
    print(f"counterfactual complete: rule={json.dumps(rule, ensure_ascii=False)}")
    print(f"target events (control): "
          f"{[t['event_id'] for t in comp['target_events_control']]}; "
          f"(treatment): {[t['event_id'] for t in comp['target_events_treatment']]}; "
          f"suppressions: {len(comp['suppressions'])}")
    print(f"total events: control {comp['control_total_events']} vs "
          f"treatment {comp['treatment_total_events']} "
          f"({comp['total_events_delta']:+d})")
    print(f"comparison: {(out or 'runs_v02/exp_counterfactual')}/comparison.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
