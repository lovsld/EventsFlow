"""Agent process entry point.

One OS process per agent. It:
- reads ONLY its own character directory (run/characters/<id>/)
- waits for its perception file in run/ipc/to_<id>/tick_<n>.json
- thinks (AgentBrain) and writes its intent to run/ipc/from_<id>/tick_<n>.json
- never opens the world log, the world snapshot, or another agent's files

Communication is via the filesystem message queues managed by the
orchestrator / World Engine — the same pattern a network transport would
use, kept local so the whole experiment is reproducible offline.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .agent_brain import AgentBrain


def wait_for(path: Path, timeout: float = 120.0) -> dict | None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                time.sleep(0.02)  # writer mid-write; retry
                continue
        time.sleep(0.02)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--agent", required=True)
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    config = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
    brain = AgentBrain(run_dir / "characters" / args.agent, seed=config["seed"])

    in_dir = run_dir / "ipc" / f"to_{args.agent}"
    out_dir = run_dir / "ipc" / f"from_{args.agent}"
    out_dir.mkdir(parents=True, exist_ok=True)

    for tick in range(1, config["ticks"] + 1):
        payload = wait_for(in_dir / f"tick_{tick}.json")
        if payload is None:
            return 2  # world never delivered: fail loudly, don't improvise
        brain.receive(payload["perception"], payload.get("action_result"))
        intent = brain.decide(payload["perception"])
        (out_dir / f"tick_{tick}.json").write_text(
            json.dumps(intent, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
