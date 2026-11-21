"""§7.4 isolation experiment (N1 gate): narrative layer on/off must not
change any run.

Runs the canonical tavern parameters (rule mode, seed 42, 20 ticks)
twice into narrative_runs/_isolation/:
  * iso_off — plain run, nothing else;
  * iso_on  — plain run, then the Observer processes its directory.
Pass condition: both world_log.json files are byte-identical to each
other AND to the frozen canonical trajectory
runs_v02/rule_v02c4_seed42/world_log.json (md5 cbf84d26...).

This harness (not part of the narrative package) is the only file in
tests_narrative/ allowed to import v02: it drives the engine.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

CANONICAL_MD5 = "cbf84d263ad01e50fca711328c1e78e2"


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    from v02.runner import run_simulation

    root = PROJECT / "narrative_runs" / "_isolation"
    results = {}
    for name in ("iso_off", "iso_on"):
        run_dir = asyncio.run(run_simulation(
            mode="rule", ticks=20, seed=42, run_name=name, out_root=root))
        results[name] = md5(run_dir / "world_log.json")
        print(f"{name}: world_log md5 {results[name]}")
    # "on": the Observer now processes the iso_on run directory.
    from narrative.observer import observe
    obs = observe(root / "iso_on", root / "iso_on_observations")
    print(f"observer on iso_on: {json.dumps(obs, ensure_ascii=False)}")
    results["iso_on_after_observer"] = md5(root / "iso_on" / "world_log.json")

    ok = (results["iso_off"] == results["iso_on"] ==
          results["iso_on_after_observer"] == CANONICAL_MD5)
    print("ISOLATION:", "PASS" if ok else "FAIL", json.dumps(results))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
