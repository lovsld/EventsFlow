"""Simulation runner: wires World Engine, Event Broker, Scheduler,
Character Agents and the SQLite audit store into a complete run.

Phase B driving (see scheduler.py / broker.py): per tick the Broker
publishes last tick's committed events into per-agent perception
envelopes (deduplicated, isolation rules unchanged) and dispatches
them onto the agents' asyncio queues; each agent task wakes on its
envelope, interprets privately, decides, and submits its intent
through the SubmissionGate; the scheduler then makes the single
World Engine commit for the tick. Semantics are Phase A's; logical
time never depends on provider latency.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .agent import CharacterAgent
from .broker import EventBroker
from .checkpoint import META_FILE, RunCheckpointer, load_checkpoint
from .config import ALL_IDS, load_all
from .providers import make_provider
from .providers.base import ProviderError
from .scheduler import QuotaWaiter, Scheduler, intent_from_decision
from .world import WorldEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_ROOT / "runs_v02"

# Phase A name, kept importable; the canonical copy lives in scheduler.py
_intent_from_decision = intent_from_decision


def preflight(mode: str) -> None:
    """Fail fast and honestly: llm mode without a credential must not
    silently degrade into fake behaviour."""
    if mode != "llm":
        return
    import os as _os
    backend = _os.environ.get("ACP_LLM_PROVIDER", "gemini")
    if backend == "openai":
        if not _os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "llm mode (openai backend) requires OPENAI_API_KEY in the "
                "environment. No key is set, so no LLM run was started "
                "(rule/mock modes work).")
    else:
        from .providers.gemini_provider import credential_available
        if not credential_available():
            raise RuntimeError(
                "llm mode (gemini backend) requires the custom.gemini "
                "connector credential or GEMINI_API_KEY. Neither is "
                "available, so no LLM run was started (rule/mock modes work).")


def render_narrative(tick_records: list[dict], names: dict[str, str]) -> str:
    lines = ["# 酒馆之夜 — 客观叙事（Narrator，仅依据世界日志）", ""]
    for rec in tick_records:
        lines.append(f"## 周期 {rec['tick']} · {rec['time']}")
        for ev in rec["events"]:
            t = ev["type"]
            who = names.get(ev["actors"][0], ev["actors"][0]) if ev.get("actors") else ""
            if t == "move":
                lines.append(f"- {who} 移动：{ev.get('from')} → {ev.get('to')}")
            elif t in ("speak", "ask", "confront"):
                suffix = "（打断）" if ev.get("interrupted") else ""
                lines.append(f"- [{ev['location']}] {ev.get('text') or who}{suffix}")
            elif t == "search":
                found = f"，发现：{', '.join(ev['found'])}" if ev.get("found") else ""
                lines.append(f"- {who} 在 {ev['location']} 搜查{found}")
            elif t == "show":
                lines.append(f"- {who} 向对方展示了 {ev.get('object')}")
            elif t == "refuse":
                lines.append(f"- {who} 拒绝继续谈 {ev.get('topic')}")
            elif t == "ambient":
                lines.append(f"- 环境：{ev.get('text')}")
            elif t in ("session_start", "session_end"):
                kind = "开始" if t == "session_start" else "结束"
                lines.append(f"- 交互 {ev.get('session')} {kind}（{ev['location']}："
                             f"{','.join(ev.get('actors', []))}）")
            elif t == "speak_failed":
                lines.append(f"- {who} 想说话，但对方已经离开")
            else:
                lines.append(f"- {who} 在 {ev.get('location')} {t}")
        lines.append("")
    return "\n".join(lines)


async def run_simulation(mode: str = "rule", ticks: int = 20, seed: int = 42,
                         run_name: str | None = None,
                         out_root: Path | None = None,
                         suppression_rules: list[dict] | None = None,
                         trace_beliefs: bool = False,
                         resume_from: str | None = None,
                         loop: bool = False) -> Path:
    """suppression_rules (Phase D): counterfactual delivery rules handed
    to the Event Broker — see broker.match_suppression. trace_beliefs:
    also write beliefs_trace.json (per-tick subjective-state snapshots),
    the raw material for experiment comparisons.

    Local runtime trio (LOCAL_DEPLOY.md): every run writes run_meta.json
    plus a per-tick checkpoint.json (and world_log.json incrementally),
    so resume_from=<run name> continues an interrupted run for `ticks`
    MORE ticks in the same directory and database; loop=True makes a
    quota-exhausted tick pause and retry instead of degrading."""
    resume = resume_from is not None
    configs = load_all()
    root = Path(out_root) if out_root else RUNS_DIR
    checkpoint = None
    resumed_from_tick = None
    if resume:
        run_id = resume_from
        run_dir = root / run_id
        meta_path = run_dir / META_FILE
        if not meta_path.exists():
            raise FileNotFoundError(
                f"cannot resume {run_id!r}: {meta_path} not found — "
                "not a resumable run directory")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("mode") != mode:
            raise ValueError(
                f"cannot resume {run_id!r}: the run was created with "
                f"mode={meta.get('mode')!r} but mode={mode!r} was "
                "requested; resume with the run's own mode")
        # The run's own seed wins: every RNG state is restored from the
        # checkpoint anyway, so the seed only labels the run.
        seed = int(meta.get("seed", seed))
        checkpoint = load_checkpoint(run_dir)
        if checkpoint is None:
            raise FileNotFoundError(
                f"cannot resume {run_id!r}: no checkpoint.json in "
                f"{run_dir} (the run predates checkpointing or never "
                "committed a tick)")
        resumed_from_tick = int(checkpoint["world_tick"])
    else:
        run_id = run_name or f"{mode}_seed{seed}"
        run_dir = root / run_id
    preflight(mode)
    (run_dir / "characters").mkdir(parents=True, exist_ok=True)

    from .persistence import Store
    store = Store(run_id, sqlite_path=str(run_dir / "acp.sqlite3"))
    if resume:
        # Drop everything the interrupted process wrote after the
        # checkpoint; the replay below rewrites it identically.
        store.rollback_after(resumed_from_tick)

    world = WorldEngine(configs, seed=seed)
    agents: dict[str, CharacterAgent] = {}
    for cid in ALL_IDS:
        provider = make_provider(mode, configs[cid], seed=seed)
        agents[cid] = CharacterAgent(configs[cid], provider,
                                     run_char_dir=run_dir / "characters" / cid,
                                     resume_dir=resume)
    broker = EventBroker(ALL_IDS, suppression_rules=suppression_rules)
    checkpointer = RunCheckpointer(run_dir, world, agents, broker)
    if resume:
        checkpointer.restore(checkpoint)
        initial_records = checkpoint["tick_records"]
        initial_deliveries = {int(t): d for t, d in
                              checkpoint["deliveries_by_tick"].items()}
        initial_last_events = checkpoint["last_events"]
    else:
        (run_dir / META_FILE).write_text(
            json.dumps({"run_id": run_id, "mode": mode, "seed": seed,
                        "characters": list(ALL_IDS),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "checkpoint_format": 1},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        # Tick-0 checkpoint: even a kill during the first tick leaves a
        # resumable run behind.
        checkpointer.write([], {}, [])
        initial_records, initial_deliveries, initial_last_events = [], {}, []
    waiter = QuotaWaiter() if loop else None
    belief_trace: list[dict] = []
    scheduler = Scheduler(
        world, broker, agents, store, ALL_IDS,
        on_tick_end=belief_trace.append if trace_beliefs else None,
        on_tick_committed=checkpointer.write,
        on_quota_pause=checkpointer.flush_pause if loop else None,
        quota_waiter=waiter,
        initial_tick_records=initial_records,
        initial_deliveries_by_tick=initial_deliveries,
        initial_last_events=initial_last_events)
    tick_records = await scheduler.run(ticks)
    if trace_beliefs:
        (run_dir / "beliefs_trace.json").write_text(
            json.dumps(belief_trace, ensure_ascii=False, indent=1), encoding="utf-8")

    names = {cid: configs[cid].name for cid in ALL_IDS}
    (run_dir / "world_log.json").write_text(
        json.dumps(tick_records, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "narrative.md").write_text(render_narrative(tick_records, names), encoding="utf-8")
    summary = {
        "run_id": run_id, "mode": mode, "seed": seed, "ticks": ticks,
        "world_tick": world.state["tick"],
        "resumed_from_tick": resumed_from_tick,
        "quota_stopped": scheduler.quota_stopped,
        "quota_waits": waiter.waits if waiter is not None else 0,
        "total_events": sum(len(r["events"]) for r in tick_records),
        "final_positions": {a: world.state["agents"][a]["location"] for a in ALL_IDS},
        "letter_seen_by_objective": world.state["objects"]["anonymous_letter"]["seen_by"],
        "final_beliefs": {cid: {b.key: {"confidence": b.confidence, "kind": b.kind,
                                        "desc": b.desc}
                                for b in agents[cid].state.beliefs} for cid in ALL_IDS},
        "memory_entries": {cid: len(agents[cid].state.perceived_event_ids) for cid in ALL_IDS},
        "db_counts": store.counts(),
        "suppression_rules": suppression_rules or [],
        "provider": mode,
        "note": (f"llm mode: real {os.environ.get('ACP_LLM_PROVIDER', 'gemini')} calls"
                 if mode == "llm" else f"{mode} mode: no external model calls"),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def run(mode: str, ticks: int, seed: int, run_name: str | None = None,
        suppression_rules: list[dict] | None = None,
        trace_beliefs: bool = False,
        resume_from: str | None = None, loop: bool = False) -> Path:
    return asyncio.run(run_simulation(mode=mode, ticks=ticks, seed=seed,
                                      run_name=run_name,
                                      suppression_rules=suppression_rules,
                                      trace_beliefs=trace_beliefs,
                                      resume_from=resume_from, loop=loop))
