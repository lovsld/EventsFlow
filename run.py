"""Run the V0.1 experiment:  python3 run.py --ticks 20 --seed 42"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.orchestrator import run_simulation  # noqa: E402

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--run-name", default=None)
    ns = ap.parse_args()
    d = run_simulation(ns.ticks, ns.seed, ns.run_name)
    print(f"run complete: {d}")
    print((d / "summary.json").read_text(encoding="utf-8"))
