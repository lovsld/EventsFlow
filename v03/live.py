"""Phase 3b live driver: a rule-mode run with the Story Engine
attached. Construction mirrors v02.runner.run_simulation's fresh
path (same classes, same order); the only additions are the
chained on_tick_committed hook and the injection of validated
candidates through WorldEngine.queue_external_event.

Pipeline timing (honest): deliveries for tick T are computed by
the broker at tick T+1, so the runtime processes tick T inside
the hook of tick T+1 and its proposals are queued then — they
commit at tick T+2's step. Two ticks of pipeline latency; the
Story Engine still never touches a decision.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from v02.agent import CharacterAgent
from v02.broker import EventBroker
from v02.checkpoint import META_FILE, RunCheckpointer
from v02.config import ALL_IDS, load_all
from v02.persistence import Store
from v02.providers import make_provider
from v02.scenario import load_scenario
from v02.scheduler import Scheduler
from v02.world import WorldEngine
from v03.scenes.runtime import SceneRuntime
from v03.story.models import StoryConfig


async def run_live(run_dir: Path, story_config: StoryConfig,
                   ticks: int = 20, seed: int = 42,
                   characters_root=None, mode: str = "rule",
                   provider: str | None = None,
                   extra_hook=None) -> dict:
    run_dir = Path(run_dir)
    (run_dir / "characters").mkdir(parents=True, exist_ok=True)
    configs = load_all(characters_root)
    ids = list(ALL_IDS) if characters_root is None else list(configs)
    scenario = load_scenario(None)
    world = WorldEngine(configs, seed=seed,
                        world_config=scenario.to_world_config())
    backend = None
    if mode == "llm":
        from v02.providers import resolve_backend
        from v02.runner import preflight
        backend = resolve_backend(provider)
        preflight(mode, provider=backend)
    agents = {}
    for cid in ids:
        prov = make_provider(mode, configs[cid], seed=seed,
                             provider=backend) if mode == "llm" \
            else make_provider(mode, configs[cid], seed=seed)
        if hasattr(prov, "scenario"):
            prov.scenario = scenario
        agents[cid] = CharacterAgent(
            configs[cid], prov,
            run_char_dir=run_dir / "characters" / cid,
            resume_dir=None, scenario=scenario)
    broker = EventBroker(ids, scenario=scenario)
    store = Store(run_dir.name, sqlite_path=str(run_dir / "acp.sqlite3"))
    checkpointer = RunCheckpointer(run_dir, world, agents, broker)
    (run_dir / META_FILE).write_text(json.dumps(
        {"run_id": run_dir.name, "mode": mode, "seed": seed,
         "backend": backend, "characters": list(ids),
         "created_at": datetime.now(timezone.utc).isoformat(),
         "checkpoint_format": 1, "engine_version": "v03-live",
         "scenario_id": scenario.scenario_id,
         "scenario": scenario.model_dump(mode="json")},
        ensure_ascii=False, indent=2), encoding="utf-8")
    checkpointer.write([], {}, [])
    for a in agents.values():
        a.evaluate_goals(0, "init")

    # --- story side ---
    from v03.story.story_engine import StoryEngine
    engine = StoryEngine(story_config, characters_root)
    sc = scenario.model_dump(mode="json")
    claims = sc.get("claims") or {}
    object_facts: dict[str, dict] = {}
    for obj_id, od in (sc.get("objects") or {}).items():
        object_facts[obj_id] = {
            "direct": [od["fact_belief"]] if od.get("fact_belief")
                      else [],
            "hearsay": [r["belief"] for r in od.get("reveals", [])]}
    engine.set_object_state(
        {oid: od.get("holder") for oid, od in
         (sc.get("objects") or {}).items() if od.get("holder")},
        {oid: od.get("location") for oid, od in
         (sc.get("objects") or {}).items() if od.get("location")})
    engine._scenario_locations = list(sc.get("locations") or [])
    basis_free = {cid for cid, cdef in claims.items()
                  if cdef.get("basis_required") is False}
    runtime = SceneRuntime(engine, basis_free_claims=basis_free)
    processed: set[int] = set()
    injected: list[dict] = []

    def enrich(record: dict) -> dict:
        for event in record.get("events", []):
            claim = event.get("claim")
            if claim and claim in claims:
                cdef = claims[claim]
                if "proposition" not in event:
                    event["proposition"] = cdef.get("proposition")
                    event["stance"] = cdef.get("stance")
                if cdef.get("speaker_kind") == "experienced":
                    event["_direct"] = True
        return record

    def process_upto(tick_records, deliveries_by_tick, upto_tick):
        for tr in tick_records:
            t = tr["tick"]
            if t in processed or t > upto_tick:
                continue
            processed.add(t)
            record = dict(tr)
            record["deliveries"] = deliveries_by_tick.get(t, [])
            result = runtime.process_tick(enrich(record), object_facts)
            for ev in result["pending_events"]:
                world.queue_external_event(ev)
                injected.append({"queued_after_tick": t, **ev})

    def hook(tick_records, deliveries_by_tick, last_events):
        checkpointer.write(tick_records, deliveries_by_tick,
                           last_events)
        if tick_records:
            process_upto(tick_records, deliveries_by_tick,
                         tick_records[-1]["tick"] - 1)
        if extra_hook is not None:
            # Phase 1 live intervention (Progress Judge): observes
            # only; may queue validated candidates through the
            # World's external queue. Default None = Phase 3b
            # behaviour unchanged.
            extra_hook(tick_records, deliveries_by_tick, world)

    scheduler = Scheduler(world, broker, agents, store, ids,
                          on_tick_committed=hook)
    tick_records = await scheduler.run(ticks)
    # final tick(s): deliveries for the last tick exist only after
    # the scheduler's final perception pass, which is not exposed
    # via the hook — process what remains with what we have.
    process_upto(tick_records, scheduler._live["deliveries_by_tick"],
                 ticks)
    runtime.finalize(ticks)
    checkpointer.write_world_log(
        tick_records, scheduler._live["deliveries_by_tick"])
    return {"run_dir": str(run_dir), "injected": injected,
            "runtime": runtime, "tick_records": tick_records}


def run_live_sync(run_dir, story_config, ticks=20, seed=42,
                  characters_root=None, mode="rule",
                  provider=None, extra_hook=None) -> dict:
    return asyncio.run(run_live(Path(run_dir), story_config,
                                ticks=ticks, seed=seed,
                                characters_root=characters_root,
                                mode=mode, provider=provider,
                                extra_hook=extra_hook))
