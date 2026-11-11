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

# V0.2-B: state-semantics version of the engine. Recorded in every
# run's run_meta.json; --resume refuses a mismatch (design risk 3).
ENGINE_VERSION = "v0.2-b"

# Phase A name, kept importable; the canonical copy lives in scheduler.py
_intent_from_decision = intent_from_decision


def preflight(mode: str, provider: str | None = None) -> None:
    """Fail fast and honestly: llm mode without a credential must not
    silently degrade into fake behaviour. The check is per selected
    backend, and the error names the exact env vars that would fix it."""
    if mode != "llm":
        return
    from .providers import resolve_backend
    backend = resolve_backend(provider)
    if backend == "gemini":
        from .providers.gemini_provider import credential_available
        if not credential_available():
            raise RuntimeError(
                "llm mode (gemini backend) requires the custom.gemini "
                "connector credential or GEMINI_API_KEY. Neither is "
                "available, so no LLM run was started (rule/mock modes work).")
    else:
        from .providers.openai_provider import PRESETS, keys_available
        if not keys_available(backend):
            preset = PRESETS[backend]
            raise RuntimeError(
                f"llm mode ({backend} backend) requires "
                f"{preset.keys_env} (comma-separated key pool) or "
                f"{preset.single_key_env} in the environment. Neither is "
                "set, so no LLM run was started (rule/mock modes work).")


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
                         loop: bool = False,
                         provider: str | None = None,
                         characters_root: Path | None = None,
                         world_config: dict | None = None) -> Path:
    """suppression_rules (Phase D): counterfactual delivery rules handed
    to the Event Broker — see broker.match_suppression. trace_beliefs:
    also write beliefs_trace.json (per-tick subjective-state snapshots),
    the raw material for experiment comparisons.

    Local runtime trio (LOCAL_DEPLOY.md): every run writes run_meta.json
    plus a per-tick checkpoint.json (and world_log.json incrementally),
    so resume_from=<run name> continues an interrupted run for `ticks`
    MORE ticks in the same directory and database; loop=True makes a
    quota-exhausted tick pause and retry instead of degrading.

    provider selects the llm backend (gemini default; deepseek / kimi /
    qwen / openai are the OpenAI-compatible pools). It is recorded in
    run_meta.json, and resuming with a different explicitly requested
    backend is rejected, like a mode mismatch.

    characters_root / world_config (V0.2-A experiment 3): a scenario is
    data — an alternative character directory and/or world-config dict
    (see v02/world_config.py) run the SAME decision code on a different
    world. Defaults reproduce the canonical tavern exactly."""
    resume = resume_from is not None
    # V0.2-B: the run's Scenario Definition — one instance, created
    # here and injected into the World Engine, the Broker, every
    # agent and the rule provider (design section 3.7).
    from .scenario import load_scenario
    meta_scenario = None
    configs = load_all(characters_root)
    # Canonical runs keep the historical cast order; a variant
    # scenario's cast is whatever its character directory defines.
    ids = list(ALL_IDS) if characters_root is None else list(configs)
    root = Path(out_root) if out_root else RUNS_DIR
    checkpoint = None
    resumed_from_tick = None
    backend = None
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
        # V0.2-B (design risk note 3): engine-version pinning. State
        # semantics changed across engine versions (beliefs, plans,
        # goals); resuming a run under a different engine version is
        # refused explicitly rather than silently producing garbage.
        if meta.get("engine_version") != ENGINE_VERSION:
            raise ValueError(
                f"cannot resume {run_id!r}: the run was created with "
                f"engine_version={meta.get('engine_version')!r} but "
                f"this code is {ENGINE_VERSION!r}; resume with the "
                "run's own engine version")
        meta_scenario = meta.get("scenario")
        if mode == "llm":
            from .providers import requested_backend
            explicit = requested_backend(provider)
            meta_backend = meta.get("backend")
            if meta_backend and explicit and explicit != meta_backend:
                raise ValueError(
                    f"cannot resume {run_id!r}: the run was created "
                    f"with backend={meta_backend!r} but "
                    f"backend={explicit!r} was requested; resume with "
                    "the run's own backend")
            backend = explicit or meta_backend or "gemini"
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
        if mode == "llm":
            from .providers import resolve_backend
            backend = resolve_backend(provider)
    preflight(mode, provider=backend)
    (run_dir / "characters").mkdir(parents=True, exist_ok=True)

    from .persistence import Store
    store = Store(run_id, sqlite_path=str(run_dir / "acp.sqlite3"))
    if resume:
        # Drop everything the interrupted process wrote after the
        # checkpoint; the replay below rewrites it identically.
        store.rollback_after(resumed_from_tick)

    if world_config is not None:
        scenario = load_scenario(world_config)
    elif meta_scenario is not None:
        scenario = load_scenario(meta_scenario)
    else:
        scenario = load_scenario(None)
    world = WorldEngine(configs, seed=seed,
                        world_config=scenario.to_world_config())
    agents: dict[str, CharacterAgent] = {}
    for cid in ids:
        agent_provider = make_provider(mode, configs[cid], seed=seed,
                                       provider=backend) \
            if mode == "llm" else make_provider(mode, configs[cid], seed=seed)
        if hasattr(agent_provider, "scenario"):
            agent_provider.scenario = scenario
        agents[cid] = CharacterAgent(configs[cid], agent_provider,
                                     run_char_dir=run_dir / "characters" / cid,
                                     resume_dir=resume, scenario=scenario)
    broker = EventBroker(ids, suppression_rules=suppression_rules,
                         scenario=scenario)
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
                        "backend": backend,
                        "characters": list(ids),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "checkpoint_format": 1,
                        # V0.2-B: engine version + the full scenario
                        # definition, so a resume rebuilds the exact
                        # same world semantics (and refuses a version
                        # mismatch instead of corrupting state).
                        "engine_version": ENGINE_VERSION,
                        "scenario_id": scenario.scenario_id,
                        "scenario": scenario.model_dump(mode="json")},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        # Tick-0 checkpoint: even a kill during the first tick leaves a
        # resumable run behind.
        checkpointer.write([], {}, [])
        initial_records, initial_deliveries, initial_last_events = [], {}, []
        # V0.2-B B2 (design 4.2): the GoalEvaluator's init pass —
        # activation conditions that already hold on private
        # knowledge (e.g. the keeper's find_missing_key) activate
        # before the first tick; the change records drain into the
        # audit chain at the first receive().
        for a in agents.values():
            a.evaluate_goals(0, "init")
    waiter = QuotaWaiter() if loop else None
    belief_trace: list[dict] = []
    scheduler = Scheduler(
        world, broker, agents, store, ids,
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

    names = {cid: configs[cid].name for cid in ids}
    (run_dir / "world_log.json").write_text(
        json.dumps(tick_records, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "narrative.md").write_text(render_narrative(tick_records, names), encoding="utf-8")
    summary = {
        "run_id": run_id, "mode": mode, "seed": seed, "ticks": ticks,
        "engine_version": ENGINE_VERSION, "scenario_id": scenario.scenario_id,
        "world_tick": world.state["tick"],
        "resumed_from_tick": resumed_from_tick,
        "quota_stopped": scheduler.quota_stopped,
        "quota_waits": waiter.waits if waiter is not None else 0,
        "total_events": sum(len(r["events"]) for r in tick_records),
        "final_positions": {a: world.state["agents"][a]["location"] for a in ids},
        # generalized in V0.2-A: every key object's objective access
        "objects_seen_by": {oid: list(o.get("seen_by", []))
                            for oid, o in world.state["objects"].items()},
        # historical field, kept when the tavern's letter exists
        **({"letter_seen_by_objective":
            world.state["objects"]["anonymous_letter"]["seen_by"]}
           if "anonymous_letter" in world.state["objects"] else {}),
        "final_beliefs": {cid: {b.key: {"confidence": b.confidence, "kind": b.kind,
                                        "desc": b.desc}
                                for b in agents[cid].state.beliefs} for cid in ids},
        "memory_entries": {cid: len(agents[cid].state.perceived_event_ids) for cid in ids},
        "db_counts": store.counts(),
        # V0.2-A: total validation-repair retries across all decisions
        # (LLM providers count them; rule/mock are always 0). Read by
        # the experiment metrics for the JSON-failure accounting.
        "llm_repairs_total": sum(
            r.get("repair_count", 0)
            for a in agents.values() for r in a.decision_records),
        "suppression_rules": suppression_rules or [],
        "provider": mode,
        "llm_backend": backend,
        "note": (f"llm mode: real {backend} calls"
                 if mode == "llm" else f"{mode} mode: no external model calls"),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def run(mode: str, ticks: int, seed: int, run_name: str | None = None,
        suppression_rules: list[dict] | None = None,
        trace_beliefs: bool = False,
        resume_from: str | None = None, loop: bool = False,
        provider: str | None = None) -> Path:
    return asyncio.run(run_simulation(mode=mode, ticks=ticks, seed=seed,
                                      run_name=run_name,
                                      suppression_rules=suppression_rules,
                                      trace_beliefs=trace_beliefs,
                                      resume_from=resume_from, loop=loop,
                                      provider=provider))
