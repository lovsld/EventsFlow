"""Run-level assembly: event graph + scene packages for a finished
run (task book §10.5/§11). Reads only the run's own products."""
from __future__ import annotations

import json
from pathlib import Path

from v03.graph.builder import GraphBuilder
from v03.graph.models import EventGraph
from v03.scenes.runtime import replay_runtime
from v03.story.architect import load_config
from v03.story.models import StoryConfig
from v03.story.story_engine import StoryEngine


def _prepare(run_dir: Path):
    meta = json.loads((run_dir / "run_meta.json")
                      .read_text(encoding="utf-8"))
    scenario = meta.get("scenario") or {}
    claims = scenario.get("claims") or {}
    object_facts: dict[str, dict] = {}
    for obj_id, od in (scenario.get("objects") or {}).items():
        object_facts[obj_id] = {
            "direct": [od["fact_belief"]] if od.get("fact_belief")
                      else [],
            "hearsay": [r["belief"] for r in od.get("reveals", [])]}
    world_log = json.loads((run_dir / "world_log.json")
                           .read_text(encoding="utf-8"))
    for record in world_log:
        for event in record.get("events", []):
            claim = event.get("claim")
            if claim and claim in claims:
                cdef = claims[claim]
                if "proposition" not in event:
                    event["proposition"] = cdef.get("proposition")
                    event["stance"] = cdef.get("stance")
                if cdef.get("speaker_kind") == "experienced":
                    event["_direct"] = True
    return world_log, object_facts


def build_graph_for_run(run_dir: str | Path, config: StoryConfig,
                        characters_root=None) -> EventGraph:
    run_dir = Path(run_dir)
    world_log, object_facts = _prepare(run_dir)
    runtime = replay_runtime(run_dir, config, characters_root)
    scene_by_event = {eid: s.scene_id
                      for s in runtime.scenes.scenes.values()
                      for eid in s.event_queue}
    builder = GraphBuilder(run_dir.name, object_facts,
                           initial_knowledge=config.initial_knowledge,
                           scene_by_event=scene_by_event)
    return builder.build(world_log, story_state=runtime.engine.state)


def build_scene_packages(run_dir: str | Path, config: StoryConfig,
                         characters_root=None) -> list[dict]:
    run_dir = Path(run_dir)
    world_log, object_facts = _prepare(run_dir)
    runtime = replay_runtime(run_dir, config, characters_root)
    out = runtime.finalize(world_log[-1]["tick"] if world_log else 0)
    graph = build_graph_for_run(run_dir, config, characters_root)
    builder_facts = GraphBuilder(run_dir.name, object_facts,
                                 initial_knowledge=config
                                 .initial_knowledge)
    builder_facts.build(world_log)
    learned_tick = {c: {f: info["tick"] for f, info in facts.items()}
                    for c, facts in builder_facts.learned.items()}
    edges_in_scene: dict[str, list] = {}
    for e in graph.edges:
        node_scene = next((n.scene_id for n in graph.nodes
                           if n.event_id == e.target), "")
        if node_scene:
            edges_in_scene.setdefault(node_scene, []).append(e)
    node_by_id = {n.event_id: n for n in graph.nodes}
    threads = runtime.engine.state.threads
    packages: list[dict] = []
    for scene in out["scenes"]:
        start, end = scene["start_tick"], scene["end_tick"] or 0
        knowledge = {}
        for char in scene["participants"]:
            facts = learned_tick.get(char, {})
            knowledge[char] = {
                "at_start": sorted(f for f, t in facts.items()
                                   if t < start),
                "at_end": sorted(f for f, t in facts.items()
                                 if t <= end)}
        functions: set[str] = set()
        for eid in scene["event_queue"]:
            node = node_by_id.get(eid)
            if node is None:
                continue
            if node.type == "search":
                functions.add("discovery")
            if node.type == "confront":
                functions.add("confrontation")
        for e in edges_in_scene.get(scene["scene_id"], []):
            functions.add({"reveals": "revelation",
                           "escalates": "escalation"}.get(
                               e.relation, e.relation))
        packages.append({
            "scene_id": scene["scene_id"],
            "location": scene["location_id"],
            "start_tick": start, "end_tick": end,
            "status": scene["status"],
            "participants": scene["participants"],
            "joined_at": scene["joined_at"],
            "left_at": scene["left_at"],
            "events": scene["event_queue"],
            "knowledge": knowledge,
            "related_threads": scene["related_threads"],
            "thread_status_at_end": {
                tid: threads[tid].status
                for tid in scene["related_threads"] if tid in threads},
            "propagations_in": [p for p in out["propagations"]
                                if p["to_scene"] == scene["scene_id"]],
            "propagations_out": [p for p in out["propagations"]
                                 if p["from_scene"]
                                 == scene["scene_id"]],
            "dramatic_functions": sorted(functions),
            "outcome": scene["outcome"]})
    return packages
