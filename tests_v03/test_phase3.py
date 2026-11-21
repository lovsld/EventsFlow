"""V0.3 Phase 3 acceptance tests (task book §19 Phase 3):
parallel locations, no bilocation, shared objective world,
perception limits, cross-scene propagation with time/space basis.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from v03.scenes.runtime import SceneRuntime, replay_runtime  # noqa: E402
from v03.scenes.scene_manager import (ConsistencyValidator,  # noqa: E402
                                      PropagationTracker, SceneManager)
from v03.story.architect import load_config  # noqa: E402
from v03.story.models import StoryConfig  # noqa: E402
from v03.story.story_engine import StoryEngine  # noqa: E402

RUN = PROJECT / "runs_v02" / "rule_v02c4_seed42"


def tick(n, positions, events):
    return {"tick": n, "time": "21:00", "positions": positions,
            "events": events, "deliveries": []}


def test_parallel_scenes_two_locations():
    mgr = SceneManager()
    mgr.process_tick(tick(1, {"a": "room1", "b": "room2"}, [
        {"event_id": "E1", "type": "speak", "actors": ["a"],
         "location": "room1"},
        {"event_id": "E2", "type": "speak", "actors": ["b"],
         "location": "room2"}]))
    mgr.process_tick(tick(2, {"a": "room1", "b": "room2"}, [
        {"event_id": "E3", "type": "search", "actors": ["b"],
         "location": "room2", "found": []}]))
    assert len(mgr.parallel_at(1)) == 2
    assert len(mgr.parallel_at(2)) == 2
    scenes = {s.location_id: s for s in mgr.scenes.values()}
    assert scenes["room1"].event_queue == ["E1"]
    assert scenes["room2"].event_queue == ["E2", "E3"]


def test_scene_closes_when_location_empties():
    mgr = SceneManager()
    mgr.process_tick(tick(1, {"a": "room1"}, [
        {"event_id": "E1", "type": "speak", "actors": ["a"],
         "location": "room1"}]))
    mgr.process_tick(tick(2, {"a": "room2"}, []))
    scene = next(iter(mgr.scenes.values()))
    assert scene.status == "closed" and scene.end_tick == 1


def test_bilocation_is_flagged():
    v = ConsistencyValidator()
    issues = v.check_tick(tick(1, {"a": "room1"}, [
        {"event_id": "E1", "type": "speak", "actors": ["a"],
         "location": "room2"}]))
    assert any(i.kind == "actor_location_mismatch" for i in issues)
    dup = v.check_tick(tick(2, {"a": "room1"}, [
        {"event_id": "E1", "type": "speak", "actors": ["a"],
         "location": "room1"},
        {"event_id": "E1", "type": "speak", "actors": ["a"],
         "location": "room1"}]))
    assert any(i.kind == "duplicate_event" for i in dup)


def test_propagation_needs_carrier_and_travel():
    tr = PropagationTracker({"owner": ["letter_exists"]})
    # soldier finds a fact in scene S1 (back room)
    tr.process_tick(
        {"tick": 3, "positions": {"soldier": "back"},
         "events": [{"event_id": "E8", "type": "search",
                     "actors": ["soldier"], "location": "back",
                     "found": ["letter"]}],
         "deliveries": []},
        {"back": "S1"}, {}, {"letter": {"direct": ["secret"],
                                        "hearsay": []}})
    # ... then speaks it in scene S2 (bar) two ticks later
    tr.process_tick(
        {"tick": 5, "positions": {"soldier": "bar"},
         "events": [{"event_id": "E9", "type": "speak",
                     "actors": ["soldier", "owner"], "location": "bar",
                     "proposition": "secret", "stance": "assert",
                     "claim": "hint"}],
         "deliveries": []},
        {"bar": "S2"}, {}, {})
    assert len(tr.propagations) == 1
    p = tr.propagations[0]
    assert p.carrier == "soldier" and p.from_scene == "S1"
    assert p.to_scene == "S2" and p.travel_ticks == 2
    # an assertion with no learning source is flagged (not hidden)
    tr.process_tick(
        {"tick": 6, "positions": {"merchant": "bar"},
         "events": [{"event_id": "E10", "type": "speak",
                     "actors": ["merchant", "owner"], "location": "bar",
                     "proposition": "secret", "stance": "assert",
                     "claim": "hint"}],
         "deliveries": []},
        {"bar": "S2"}, {}, {})
    assert any(i.kind == "unexplained_assertion" for i in tr.issues)


def test_tavern_replay_scenes_and_propagation():
    cfg = load_config(PROJECT / "stories" / "tavern_secret.json")
    rt = replay_runtime(RUN, cfg, PROJECT / "characters_v02")
    out = rt.finalize(20)
    assert len(out["scenes"]) >= 3
    assert len(rt.scenes.parallel_at(3)) >= 2  # bar + back_room overlap
    props = [p for p in out["propagations"]
             if p["fact"] == "mayor_embezzled"
             and p["carrier"] == "soldier"]
    assert props and all(p["travel_ticks"] >= 1 for p in props)
    assert out["consistency_issues"] == []
    assert out["propagation_issues"] == []
    # runtime surfaces the Phase 2 proposals as pending world events
    assert len(out["pending_events"]) == 3
    assert all(p["source"] == "story_engine"
               for p in out["pending_events"])


def test_runtime_does_not_touch_world_log():
    import hashlib
    before = hashlib.md5((RUN / "world_log.json").read_bytes()).hexdigest()
    cfg = load_config(PROJECT / "stories" / "tavern_secret.json")
    replay_runtime(RUN, cfg, PROJECT / "characters_v02")
    after = hashlib.md5((RUN / "world_log.json").read_bytes()).hexdigest()
    assert before == after == "cbf84d263ad01e50fca711328c1e78e2"
