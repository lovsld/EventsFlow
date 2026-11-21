"""Phase 6 controlled experiments (task book §19 Phase 6).

Matrix (approved by Hu 2026-11-21): tavern, 20 ticks.
- rule: Story Engine OFF/ON x seeds {42, 7, 123}
- rule goal-variant (soldier honor-first): OFF/ON x seed 42
- llm (deepseek): OFF/ON x seeds {42, 7}, hard token cap —
  the batch stops and reports if cumulative usage exceeds it.

Every run gets the full analysis (story state, scenes, graph,
plan, continuity) and one metrics row. The autonomy red-line
metric — Story Engine directly changed a character decision —
is 0 by construction (no code path exists); it is recorded per
run from the structural fact, not asserted per event.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from v02.runner import run_simulation
from v03.graph.packages import build_graph_for_run, build_scene_packages
from v03.live import run_live_sync
from v03.planning.exporter import export_run
from v03.scenes.runtime import replay_runtime
from v03.story.architect import load_config

ROOT = Path(__file__).resolve().parent.parent
PHASE6 = ROOT / "v03_runs" / "phase6"
CONFIG = ROOT / "stories" / "tavern_secret.json"
TAVERN = ROOT / "characters_v02"
VARIANT = ROOT / "characters_v03_goalvar"
TOKEN_CAP_IN = 1_200_000
TOKEN_CAP_OUT = 150_000


def _usage(run_dir: Path) -> dict:
    db = run_dir / "acp.sqlite3"
    if not db.exists():
        return {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    total_in = total_out = calls = 0
    try:
        con = sqlite3.connect(db)
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        if "agent_decisions" in tables:
            total_in, total_out, calls = con.execute(
                "SELECT COALESCE(SUM(input_tokens), 0), "
                "COALESCE(SUM(output_tokens), 0), COUNT(*) "
                "FROM agent_decisions").fetchone()
        con.close()
    except sqlite3.Error:
        pass
    return {"input_tokens": total_in, "output_tokens": total_out,
            "calls": calls}


def metrics_for_run(run_dir: Path, characters_root: Path,
                    engine_on: bool, injected: list | None) -> dict:
    cfg = load_config(CONFIG)
    rt = replay_runtime(run_dir, cfg, characters_root)
    out = rt.finalize(20)
    graph = build_graph_for_run(run_dir, cfg, characters_root)
    export = export_run(run_dir, cfg, characters_root,
                        out_dir=run_dir)
    wl = json.loads((run_dir / "world_log.json")
                    .read_text(encoding="utf-8"))
    speaks: dict[str, int] = {}
    for record in wl:
        for e in record["events"]:
            if e["type"] in ("speak", "ask", "confront") and \
                    e.get("actors"):
                speaks[e["actors"][0]] = speaks.get(
                    e["actors"][0], 0) + 1
    story_events = [e for r in wl for e in r["events"]
                    if e.get("source") == "story_engine"]
    delivered_ids = set()
    for record in wl:
        for d in record.get("deliveries", []):
            delivered_ids.update(d.get("delivered_event_ids", []))
    threads = rt.engine.state.threads
    return {
        "run": run_dir.name, "engine_on": engine_on,
        "events_total": sum(len(r["events"]) for r in wl),
        "speaks": speaks,
        "threads_final": {tid: t.status for tid, t in
                          threads.items()},
        "main_progress": threads["main_embezzlement"].progress
        if "main_embezzlement" in threads else None,
        "conflicts_peak_severity": max(
            (c.severity for c in
             rt.engine.state.conflicts.values()), default=0),
        "propagations": len(out["propagations"]),
        "graph_edges": graph.edge_counts(),
        "arc_final_stage": export["plan"]["arc"]["final_stage"],
        "candidates_proposed": sum(
            1 for c in rt.engine.candidates_log
            if c.validation.get("passed")),
        "injected_committed": len(story_events),
        "injected_perceived": sum(
            1 for e in story_events
            if e["event_id"] in delivered_ids),
        "continuity_errors": export["continuity"]["errors"],
        "story_engine_decision_changes": 0,
        "usage": _usage(run_dir)}


def _record(metrics_path: Path, row: dict) -> None:
    data = []
    if metrics_path.exists():
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
    data = [d for d in data if d["run"] != row["run"]] + [row]
    metrics_path.write_text(json.dumps(data, ensure_ascii=False,
                                       indent=1), encoding="utf-8")


def run_rule_batch() -> None:
    PHASE6.mkdir(parents=True, exist_ok=True)
    metrics_path = PHASE6 / "STORY_METRICS.json"
    cfg = load_config(CONFIG)
    jobs = []
    for seed in (42, 7, 123):
        jobs.append((f"rule_off_seed{seed}", "off", seed, TAVERN))
        jobs.append((f"rule_on_seed{seed}", "on", seed, TAVERN))
    jobs.append(("rule_off_goalvar_seed42", "off", 42, VARIANT))
    jobs.append(("rule_on_goalvar_seed42", "on", 42, VARIANT))
    for name, mode, seed, chars in jobs:
        run_dir = PHASE6 / name
        if (run_dir / "world_log.json").exists():
            print("skip existing", name)
            continue
        if mode == "off":
            asyncio.run(run_simulation(
                mode="rule", ticks=20, seed=seed, run_name=name,
                out_root=PHASE6, characters_root=chars))
            injected = None
        else:
            res = run_live_sync(run_dir, cfg, ticks=20, seed=seed,
                                characters_root=chars)
            injected = res["injected"]
        row = metrics_for_run(run_dir, chars, mode == "on", injected)
        row.update({"provider": "rule", "seed": seed,
                    "variant": chars.name})
        _record(metrics_path, row)
        print("done", name, row["events_total"], "events")


def run_llm_batch() -> None:
    PHASE6.mkdir(parents=True, exist_ok=True)
    metrics_path = PHASE6 / "STORY_METRICS.json"
    cfg = load_config(CONFIG)
    done = []
    if metrics_path.exists():
        done = json.loads(metrics_path.read_text(encoding="utf-8"))
    used_in = sum(d.get("usage", {}).get("input_tokens", 0)
                  for d in done if d.get("provider") == "deepseek")
    used_out = sum(d.get("usage", {}).get("output_tokens", 0)
                   for d in done if d.get("provider") == "deepseek")
    jobs = []
    for seed in (42, 7):
        jobs.append((f"llm_off_seed{seed}", "off", seed))
        jobs.append((f"llm_on_seed{seed}", "on", seed))
    for name, mode, seed in jobs:
        if used_in > TOKEN_CAP_IN or used_out > TOKEN_CAP_OUT:
            print(f"BUDGET CAP HIT (in={used_in}, out={used_out}); "
                  f"skipping {name}")
            continue
        run_dir = PHASE6 / name
        if (run_dir / "world_log.json").exists():
            print("skip existing", name)
            continue
        if mode == "off":
            asyncio.run(run_simulation(
                mode="llm", ticks=20, seed=seed, run_name=name,
                out_root=PHASE6, provider="deepseek",
                characters_root=TAVERN))
            injected = None
        else:
            res = run_live_sync(run_dir, cfg, ticks=20, seed=seed,
                                characters_root=TAVERN, mode="llm",
                                provider="deepseek")
            injected = res["injected"]
        row = metrics_for_run(run_dir, TAVERN, mode == "on", injected)
        row.update({"provider": "deepseek", "seed": seed,
                    "variant": TAVERN.name})
        _record(metrics_path, row)
        used_in += row["usage"]["input_tokens"]
        used_out += row["usage"]["output_tokens"]
        print("done", name, row["events_total"], "events, usage",
              row["usage"])


if __name__ == "__main__":
    import sys
    if sys.argv[1] == "rule":
        run_rule_batch()
    elif sys.argv[1] == "llm":
        run_llm_batch()
