"""Narrative Planner + Setup/Payoff registry (task book §12).

The plan ORGANIZES what happened for the novel stage; it does not
rewrite it: chapters group scene packages by arc stage, key events
are graph-edge endpoints, and every setup carries its payoff or is
honestly listed as still open.
"""
from __future__ import annotations

import json
from pathlib import Path

from v03.graph.packages import _prepare, build_graph_for_run
from v03.planning.arc_manager import ArcManager
from v03.scenes.runtime import replay_runtime
from v03.story.models import StoryConfig

STAGE_LABELS = {"setup": "铺垫", "development": "发展",
                "escalation": "升级", "climax": "高潮",
                "aftermath": "余波", "resolution": "收束"}


def _main_thread(engine) -> str:
    threads = engine.state.threads
    if not threads:
        return ""
    return max(threads.values(),
               key=lambda t: len(t.related_events)).thread_id


def build_plan(run_dir: str | Path, config: StoryConfig,
               characters_root=None) -> dict:
    run_dir = Path(run_dir)
    world_log, object_facts = _prepare(run_dir)
    # pass 1: find the main thread and the graph
    runtime = replay_runtime(run_dir, config, characters_root)
    main_id = _main_thread(runtime.engine)
    graph = build_graph_for_run(run_dir, config, characters_root)
    main_events = set()
    if main_id:
        main_events = set(runtime.engine.state.threads[main_id]
                          .related_events)
    node_tick = {n.event_id: n.tick for n in graph.nodes}

    # pass 2: per-tick signals -> arc
    from v03.story.story_engine import StoryEngine
    engine = StoryEngine(config, characters_root)
    engine.set_object_state(
        {oid: od.get("holder") for oid, od in
         _scenario_objects(run_dir).items() if od.get("holder")},
        {oid: od.get("location") for oid, od in
         _scenario_objects(run_dir).items() if od.get("location")})
    engine._scenario_locations = _scenario_locations(run_dir)
    arc = ArcManager(main_thread_id=main_id)
    ladder_events: list[str] = []
    prop_total = 0
    seen_props: set[tuple] = set()
    rt2 = replay_runtime(run_dir, config, characters_root)
    props_by_tick: dict[int, int] = {}
    for p in rt2.propagation.propagations:
        props_by_tick[p.spoke_at] = props_by_tick.get(p.spoke_at,
                                                      0) + 1
    escalates = [e for e in graph.edges if e.relation == "escalates"]
    for record in world_log:
        tick = record["tick"]
        engine.observe_tick(record, object_facts)
        ladder_events = [e.target for e in escalates
                         if node_tick.get(e.target, 99) <= tick
                         and e.target in main_events]
        prop_total += props_by_tick.get(tick, 0)
        arc.observe(tick, {
            "threads": {tid: {"status": t.status,
                              "events": len(t.related_events)}
                        for tid, t in engine.state.threads.items()},
            "conflicts": [{"id": c.conflict_id,
                           "severity": c.severity}
                          for c in engine.state.conflicts.values()],
            "propagations": prop_total,
            "developed_any": any(
                t.last_developed_at == tick
                for t in engine.state.threads.values()),
            "main_ladder": len(ladder_events),
            "main_ladder_events": ladder_events})
    arc_report = arc.report(world_log[-1]["tick"] if world_log else 0)

    # chapters = arc-stage segments; scenes are long-lived location
    # registrations, so a chapter SLICES the scenes overlapping its
    # tick range rather than grouping whole scenes
    from v03.graph.packages import build_scene_packages
    packages = build_scene_packages(run_dir, config, characters_root)
    stage_at = {int(k): v for k, v in arc_report["stage_at"].items()}
    segments: list[tuple[str, int, int]] = []
    for tick in sorted(stage_at):
        stage = stage_at[tick]
        if segments and segments[-1][0] == stage and \
                segments[-1][2] == tick - 1:
            segments[-1] = (stage, segments[-1][1], tick)
        else:
            segments.append((stage, tick, tick))
    edge_nodes = {e.source for e in graph.edges} | \
        {e.target for e in graph.edges}
    chapters: list[dict] = []
    for stage, seg_start, seg_end in segments:
        slices = []
        threads: list[str] = []
        locations: list[str] = []
        for pkg in packages:
            in_range = [eid for eid in pkg["events"]
                        if seg_start <= node_tick.get(eid, -1)
                        <= seg_end]
            if not in_range:
                continue
            slices.append({"scene_id": pkg["scene_id"],
                           "location": pkg["location"],
                           "events": in_range})
            if pkg["location"] not in locations:
                locations.append(pkg["location"])
            for tid in pkg["related_threads"]:
                if tid not in threads:
                    threads.append(tid)
        keys = [eid for s in slices for eid in s["events"]
                if eid in edge_nodes]
        keys.sort(key=lambda eid: node_tick.get(eid, 0))
        chapters.append({
            "chapter_id": f"CH{len(chapters) + 1:02d}",
            "title": f"第{len(chapters) + 1}章 · "
                     f"{STAGE_LABELS.get(stage, stage)}",
            "stage": stage, "tick_range": [seg_start, seg_end],
            "scene_slices": slices, "locations": locations,
            "threads": threads, "key_events": keys})

    setups = _setup_payoffs(runtime.engine, graph, object_facts,
                            world_log)
    return {"run_id": run_dir.name, "main_thread": main_id,
            "arc": arc_report, "chapters": chapters,
            "setups": setups,
            "threads": {tid: {"status": t.status,
                              "progress": t.progress,
                              "events": len(t.related_events)}
                        for tid, t in
                        runtime.engine.state.threads.items()},
            "edge_counts": graph.edge_counts()}


def _scenario_objects(run_dir: Path) -> dict:
    meta = json.loads((run_dir / "run_meta.json")
                      .read_text(encoding="utf-8"))
    return (meta.get("scenario") or {}).get("objects") or {}


def _scenario_locations(run_dir: Path) -> list:
    meta = json.loads((run_dir / "run_meta.json")
                      .read_text(encoding="utf-8"))
    return list((meta.get("scenario") or {}).get("locations") or [])


def _setup_payoffs(engine, graph, object_facts, world_log) -> list[dict]:
    tick_of = {}
    for record in world_log:
        for e in record["events"]:
            tick_of[e["event_id"]] = record["tick"]
    reveals_from = {e.source: e for e in graph.edges
                    if e.relation == "reveals"}
    out: list[dict] = []
    for t in engine.state.threads.values():
        for q in t.questions:
            out.append({
                "id": f"setup_q_{t.thread_id}_{q.id}",
                "kind": "question", "thread": t.thread_id,
                "description": q.text,
                "planted_tick": tick_of.get(t.related_events[0], 0)
                if t.related_events else 0,
                "payoff_tick": q.answered_at,
                "status": "paid_off" if q.answered_at is not None
                          else "open"})
    for obj, facts in object_facts.items():
        find_tick = None
        for record in world_log:
            for e in record["events"]:
                if e.get("type") == "search" and \
                        obj in (e.get("found") or []):
                    find_tick = record["tick"]
                    find_event = e["event_id"]
                    break
            if find_tick is not None:
                break
        if find_tick is None:
            continue
        reveal = reveals_from.get(find_event)
        out.append({
            "id": f"setup_obj_{obj}", "kind": "object",
            "thread": "",
            "description": f"object {obj} discovered",
            "planted_tick": find_tick,
            "payoff_tick": tick_of.get(reveal.target)
            if reveal else None,
            "status": "paid_off" if reveal else "open"})
    return out
