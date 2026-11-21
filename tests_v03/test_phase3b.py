"""V0.3 Phase 3b tests: the world-side injection queue (approved
patch) and the live A/B. OFF state must stay byte-identical."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from v02.config import load_all  # noqa: E402
from v02.world import WorldEngine  # noqa: E402

CANONICAL = PROJECT / "runs_v02" / "rule_v02c4_seed42" / "world_log.json"


def _world() -> WorldEngine:
    return WorldEngine(load_all(PROJECT / "characters_v02"), seed=42)


def test_empty_queue_step_has_no_extra_events():
    w = _world()
    events = w.step({})
    assert all(e.get("source") != "story_engine" for e in events)
    assert not w._external_queue


def test_queued_event_commits_with_world_id():
    w = _world()
    w.queue_external_event({"type": "ambient", "subtype": "trace_event",
                             "actors": [], "location": "bar",
                             "text": "痕迹。", "source": "story_engine",
                             "candidate_id": "ECX",
                             "causal_basis": ["E0001"]})
    events = w.step({})
    mine = [e for e in events if e.get("candidate_id") == "ECX"]
    assert len(mine) == 1
    assert mine[0]["event_id"].startswith("E") and \
        mine[0]["event_id"] != "PENDING"
    assert mine[0]["tick"] == 1 and mine[0]["causal_basis"] == ["E0001"]
    # queue drained: the next step does not re-commit it
    events2 = w.step({})
    assert not any(e.get("candidate_id") == "ECX" for e in events2)


def test_live_run_injects_and_delivers(tmp_path):
    from v03.live import run_live_sync
    from v03.story.architect import load_config
    cfg = load_config(PROJECT / "stories" / "tavern_secret.json")
    res = run_live_sync(tmp_path / "live12", cfg, ticks=12, seed=42,
                        characters_root=PROJECT / "characters_v02")
    wl = res["tick_records"]
    story = [(t["tick"], e) for t in wl for e in t["events"]
             if e.get("source") == "story_engine"]
    assert story  # at least the trace event by tick 12
    arrival = [e for _, e in story if e.get("subtype")
               == "external_arrival"]
    assert arrival  # mayor's agent committed around tick 10
    delivered = set()
    for t in wl:
        for d in t.get("deliveries", []):
            delivered.update(d.get("delivered_event_ids", []))
    assert arrival[0]["event_id"] in delivered  # perceived at the bar
