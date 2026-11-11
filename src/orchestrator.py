"""Orchestrator — owns the World Engine and the tick barrier.

Per tick:
 1. snapshot world
 2. Event Broker builds each agent's perception from last tick's committed
    events (+ that agent's own action result)
 3. perceptions are written to the agents' inbox queues; the three agent
    PROCESSES think concurrently and write intents back
 4. World Engine + Action Resolver commit the tick (single writer)
 5. everything is appended to the objective world log

The Narrator (render_narrative) runs only AFTER the simulation, reads only
the objective log, and invents nothing.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .event_broker import deliver
from .world_engine import WorldEngine

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ["A", "B", "C"]


def wait_intent(path: Path, timeout: float = 120.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                time.sleep(0.02)
                continue
        time.sleep(0.02)
    raise TimeoutError(f"agent intent never arrived: {path}")


def render_narrative(run_dir: Path, tick_records: list[dict]) -> str:
    lines = ["# 酒馆之夜 — 客观叙事（Narrator，仅依据世界日志）", ""]
    for rec in tick_records:
        lines.append(f"## 周期 {rec['tick']} · {rec['time']}")
        for ev in rec["events"]:
            t = ev["type"]
            who = ",".join(ev.get("actors", [])) or "—"
            if t in ("speak", "ask", "confront"):
                flag = "（打断）" if ev.get("interrupted") else ""
                lines.append(f"- [{ev['location']}] {ev.get('text', '')}{flag}")
            elif t == "move":
                lines.append(f"- {who} 移动：{ev.get('from')} → {ev.get('to')}")
            elif t == "search":
                found = f"，发现：{','.join(ev['found'])}" if ev.get("found") else "，一无所获"
                lines.append(f"- {who} 在 {ev['location']} 搜查{found}")
            elif t == "show":
                lines.append(f"- {who} 向对方展示了 {ev.get('object')}")
            elif t == "refuse":
                lines.append(f"- {who} 拒绝继续谈 {ev.get('topic')}")
            elif t == "ambient":
                lines.append(f"- 环境：{ev.get('text')}")
            elif t in ("session_start", "session_end"):
                lines.append(f"- 交互 {ev['session']} {'开始' if t == 'session_start' else '结束'}"
                             f"（{ev['location']}：{who}）")
            elif t in ("observe", "listen", "work", "wait"):
                lines.append(f"- {who} 在 {ev['location']} {t}")
        lines.append("")
    return "\n".join(lines)


def run_simulation(ticks: int = 20, seed: int = 42, run_name: str | None = None) -> Path:
    run_name = run_name or f"run_seed{seed}"
    run_dir = ROOT / "runs" / run_name
    if run_dir.exists():
        shutil.rmtree(run_dir)
    (run_dir / "ipc").mkdir(parents=True)
    shutil.copytree(ROOT / "characters", run_dir / "characters")
    (run_dir / "run_config.json").write_text(
        json.dumps({"ticks": ticks, "seed": seed}), encoding="utf-8")

    profiles = {a: json.loads((ROOT / "characters" / a / "profile.json").read_text(encoding="utf-8"))
                for a in AGENTS}
    world = WorldEngine(profiles, seed=seed)

    procs = {}
    for a in AGENTS:
        (run_dir / "ipc" / f"to_{a}").mkdir(parents=True, exist_ok=True)
        (run_dir / "ipc" / f"from_{a}").mkdir(parents=True, exist_ok=True)
        procs[a] = subprocess.Popen(
            [sys.executable, "-m", "src.agent_process", "--run-dir", str(run_dir), "--agent", a],
            cwd=str(ROOT))

    tick_records, last_events = [], []
    try:
        for tick in range(1, ticks + 1):
            snap = world.snapshot()
            snap["tick"] = tick  # perceptions are FOR this tick, about last tick's events
            perceptions, deliveries = deliver(snap, last_events, AGENTS)
            for a in AGENTS:
                payload = {"perception": perceptions[a],
                           "action_result": world.state["last_action_results"].get(a)}
                (run_dir / "ipc" / f"to_{a}" / f"tick_{tick}.json").write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            intents = {a: wait_intent(run_dir / "ipc" / f"from_{a}" / f"tick_{tick}.json")
                       for a in AGENTS}
            last_events = world.step(intents)
            tick_records.append({
                "tick": tick, "time": world.state["time"],
                "positions": {a: world.state["agents"][a]["location"] for a in AGENTS},
                "intents": intents, "events": last_events, "deliveries": deliveries,
            })
    finally:
        for a, p in procs.items():
            rc = p.wait(timeout=60)
            if rc != 0:
                raise RuntimeError(f"agent {a} exited with {rc}")

    (run_dir / "world_log.json").write_text(
        json.dumps(tick_records, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "narrative.md").write_text(render_narrative(run_dir, tick_records), encoding="utf-8")
    summary = {
        "ticks": ticks, "seed": seed,
        "final_positions": tick_records[-1]["positions"],
        "total_events": sum(len(r["events"]) for r in tick_records),
        "letter_seen_by_objective": world.state["objects"]["anonymous_letter"]["seen_by"],
        "final_beliefs": {},
    }
    for a in AGENTS:
        st = json.loads((run_dir / "characters" / a / "state.json").read_text(encoding="utf-8"))
        summary["final_beliefs"][a] = st["beliefs"]
        summary[f"memory_entries_{a}"] = len(st["perceived_event_ids"])
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    return run_dir


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--run-name", default=None)
    ns = ap.parse_args()
    d = run_simulation(ns.ticks, ns.seed, ns.run_name)
    print(f"run complete: {d}")
