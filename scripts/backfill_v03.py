"""Backfill v03 read-only analysis for runs that predate it
(upgrade Phase 2 prep): story state, scenes, event graph, scene
packages into v03_runs/<run>/. Deterministic, zero LLM quota.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from v03.graph.packages import (build_graph_for_run,          # noqa: E402
                                build_scene_packages)
from v03.persistence import StoryStore                      # noqa: E402
from v03.scenes.runtime import replay_runtime               # noqa: E402
from v03.story.architect import load_config                 # noqa: E402
from v03.story.story_engine import StoryEngine              # noqa: E402

JOBS = {"c5_lighthouse_seed7": "stories/lighthouse_watch.json",
        "clinic_v02cfix_seed42": "stories/clinic_fever.json"}


def main() -> int:
    for run, cfg_rel in JOBS.items():
        run_dir = ROOT / "runs_v02" / run
        out = ROOT / "v03_runs" / run
        out.mkdir(parents=True, exist_ok=True)
        cfg = load_config(ROOT / cfg_rel)
        engine = StoryEngine.replay(run_dir, cfg)
        StoryStore(out / "story.sqlite3").save(engine.state)
        (out / "story_state.json").write_text(json.dumps(
            engine.state.model_dump(), ensure_ascii=False,
            indent=1), encoding="utf-8")
        runtime = replay_runtime(run_dir, cfg)
        wl = json.loads((run_dir / "world_log.json")
                        .read_text(encoding="utf-8"))
        (out / "scenes.json").write_text(json.dumps(
            runtime.finalize(wl[-1]["tick"] if wl else 0),
            ensure_ascii=False, indent=1), encoding="utf-8")
        graph = build_graph_for_run(run_dir, cfg)
        (out / "event_graph.json").write_text(json.dumps(
            graph.model_dump(), ensure_ascii=False, indent=1),
            encoding="utf-8")
        packages = build_scene_packages(run_dir, cfg)
        (out / "scene_packages.json").write_text(json.dumps(
            packages, ensure_ascii=False, indent=1),
            encoding="utf-8")
        threads = engine.state.threads
        summary = {tid: {"status": str(t.status),
                         "progress": t.progress}
                   for tid, t in threads.items()}
        print(f"{run}: threads={summary} "
              f"graph_edges={len(graph.edges)} "
              f"packages={len(packages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
