"""Phase B tests for ACP V0.2 (pytest).

Covered: Broker deduplication (repeat publish delivers once, drops are
audited in the dedup_drops table), the Interaction Manager lifecycle
(interruption via the Resolver's floor, listener stance + delivery,
leave + membership update), the SubmissionGate contract (the unified
submission path), and an end-to-end regression: the event-driven
scheduler's rule run must reproduce the Phase A baseline exactly.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from v02.broker import EventBroker
from v02.config import load_all
from v02.persistence import DedupDropRow, DeliveryRow, Store
from v02.scheduler import SubmissionGate
from v02.world import WorldEngine

ROOT = Path(__file__).resolve().parent.parent
IDS = ["owner", "merchant", "soldier"]
WAIT = {"verb": "wait"}


def make_world() -> WorldEngine:
    return WorldEngine(load_all(), seed=42)


def place(world: WorldEngine, **locs: str) -> None:
    for aid, loc in locs.items():
        world.state["agents"][aid]["location"] = loc


# ---------- Broker deduplication ----------
def test_broker_dedup_delivers_once_and_audits_drops(tmp_path):
    world = make_world()
    place(world, merchant="bar", soldier="tables")   # owner starts at bar
    snap = world.snapshot()
    events = [{"event_id": "E0001", "tick": 1, "type": "speak",
               "actors": ["owner"], "location": "bar",
               "text": "镇长那笔钱……", "topic": "funds"}]
    broker = EventBroker(IDS)
    store = Store("dedup_test", sqlite_path=str(tmp_path / "acp.sqlite3"))

    records1 = broker.publish(snap, events)
    store.record_deliveries(records1)
    by_agent = {r["agent_id"]: r for r in records1}
    # same room: full delivery; adjacent room: muffled existence only
    assert by_agent["merchant"]["delivered_event_ids"] == ["E0001"]
    assert by_agent["soldier"]["delivered_event_ids"] == []
    assert by_agent["soldier"]["muffled_event_ids"] == ["E0001"]

    # publishing the SAME batch again must not deliver anything twice
    records2 = broker.publish(snap, events)
    for r in records2:
        assert r["delivered_event_ids"] == [] and r["muffled_event_ids"] == []
    perc = broker.pending_perceptions()
    assert perc["merchant"]["events"] == [] and perc["soldier"]["muffled"] == []

    # every suppression is audit-recorded and queryable in the DB
    drops = broker.drain_dedup_drops()
    assert {(d["agent_id"], d["kind"]) for d in drops} == {
        ("owner", "full"), ("merchant", "full"), ("soldier", "muffled")}
    assert all(d["event_id"] == "E0001" and d["reason"] == "already_delivered"
               for d in drops)
    store.record_dedup_drops(drops)
    assert store.counts()["dedup_drops"] == 3
    with store.Session() as s:
        rows = s.query(DeliveryRow).all()
        hits = [r for r in rows if "E0001" in (r.delivered_event_ids or "")]
        assert {r.agent_id for r in hits} == {"owner", "merchant"}
        drops_rows = s.query(DedupDropRow).filter_by(event_id="E0001").all()
        assert len(drops_rows) == 3


# ---------- Interaction Manager: interruption ----------
def test_interrupt_second_speaker_in_session_is_flagged():
    world = make_world()
    place(world, merchant="bar")
    world.step({cid: dict(WAIT) for cid in IDS})          # session forms at bar
    mgr = world.interactions
    session = mgr.active_session_at("bar")
    assert session is not None
    assert set(session["participants"]) == {"owner", "merchant"}

    events = world.step({
        "owner": {"verb": "speak", "target": "merchant", "text": "先听我说。",
                  "topic": "funds"},
        "merchant": {"verb": "speak", "target": "owner", "text": "我也要说。",
                     "topic": "funds"},
        "soldier": dict(WAIT)})
    speeches = [e for e in events if e["type"] == "speak"]
    assert len(speeches) == 2
    assert sorted(e["interrupted"] for e in speeches) == [False, True]
    transcript = mgr.active_session_at("bar")["transcript"]
    assert len(transcript) == 2
    assert sum(1 for t in transcript if t["interrupted"]) == 1
    assert any(e["kind"] == "interrupt"
               for e in mgr.active_session_at("bar")["lifecycle"])


# ---------- Interaction Manager: listen ----------
def test_listen_registers_listener_and_receives_utterance():
    world = make_world()
    place(world, merchant="bar", soldier="bar")
    world.step({cid: dict(WAIT) for cid in IDS})          # 3-person session at bar
    mgr = world.interactions

    events = world.step({
        "owner": {"verb": "speak", "target": "merchant", "text": "那封信的事……",
                  "topic": "letter"},
        "merchant": dict(WAIT),
        "soldier": {"verb": "listen"}})
    session = mgr.active_session_at("bar")
    assert mgr.is_listener(session, "soldier")
    assert "soldier" in session["listeners"]
    assert any(e["kind"] == "listen" and e["agent"] == "soldier"
               for e in session["lifecycle"])

    # the listener receives the session utterance in full (room rule)
    broker = EventBroker(IDS)
    records = broker.publish(world.snapshot(), events)
    by_agent = {r["agent_id"]: r for r in records}
    speak = next(e for e in events if e["type"] == "speak")
    assert speak["event_id"] in by_agent["soldier"]["delivered_event_ids"]
    perc = broker.pending_perceptions()["soldier"]
    heard = next(e for e in perc["events"] if e["event_id"] == speak["event_id"])
    assert heard["text"] == "那封信的事……"


# ---------- Interaction Manager: leave ----------
def test_leave_updates_membership_and_stops_full_delivery():
    world = make_world()
    place(world, merchant="bar", soldier="bar")
    world.step({cid: dict(WAIT) for cid in IDS})          # 3-person session at bar
    mgr = world.interactions
    old_session_id = mgr.active_session_at("bar")["session_id"]

    world.step({"owner": dict(WAIT), "merchant": dict(WAIT),
                "soldier": {"verb": "move", "target_location": "tables"}})
    # membership updated: the bar session is now owner+merchant only
    session = mgr.active_session_at("bar")
    assert session is not None and session["session_id"] != old_session_id
    assert session["participants"] == ["merchant", "owner"]
    assert mgr.session_of("soldier") is None
    # the closed session's lifecycle records the departure
    closed = next(s for s in mgr.history if s["session_id"] == old_session_id)
    assert any(e["kind"] == "left" and e["agent"] == "soldier"
               for e in closed["lifecycle"])
    assert any(e["kind"] == "ended" for e in closed["lifecycle"])

    # after leaving, bar speech reaches the soldier only as a muffled notice
    events = world.step({
        "owner": {"verb": "speak", "target": "merchant", "text": "只说给你听。",
                  "topic": "secret"},
        "merchant": dict(WAIT), "soldier": dict(WAIT)})
    speak = next(e for e in events if e["type"] == "speak")
    broker = EventBroker(IDS)
    records = broker.publish(world.snapshot(), events)
    by_agent = {r["agent_id"]: r for r in records}
    assert speak["event_id"] not in by_agent["soldier"]["delivered_event_ids"]
    assert speak["event_id"] in by_agent["soldier"]["muffled_event_ids"]
    perc = broker.pending_perceptions()["soldier"]
    assert all(e["event_id"] != speak["event_id"] for e in perc["events"])


# ---------- unified submission ----------
def test_submission_gate_contract():
    async def scenario():
        gate = SubmissionGate(["a", "b"])
        with pytest.raises(RuntimeError):
            gate.submit("a", {"verb": "wait"})            # window closed
        gate.open_window()
        with pytest.raises(ValueError):
            gate.submit("mallory", {"verb": "wait"})      # unknown agent
        gate.submit("a", {"verb": "wait"})
        with pytest.raises(RuntimeError):
            gate.submit("a", {"verb": "wait"})            # duplicate in one tick
        assert not gate.all_submitted
        gate.submit("b", {"verb": "observe"})
        await gate.wait_all()
        intents, _records = gate.close_and_collect()
        assert set(intents) == {"a", "b"}
        with pytest.raises(RuntimeError):
            gate.submit("a", {"verb": "wait"})            # window closed again
    asyncio.run(scenario())


# ---------- end-to-end regression vs the Phase A baseline ----------
def test_scheduler_rule_run_deterministic_across_hash_seeds(tmp_path):
    # Regression contract after the 2026-11-10 determinism fix (approved
    # by Hu): the rule provider enumerates movement destinations with
    # sorted(), so a rule run must be bit-for-bit identical under ANY
    # PYTHONHASHSEED. Before the fix, ADJACENCY set iteration made the
    # seeded RNG's jitter land on different destinations per process
    # (see PHASE_B_REPORT.md); the historical 91-event Phase A baseline
    # was one of the two hash-dependent branches. The deterministic
    # trajectory was the 85-event branch, with the same emergent chain:
    # the soldier reads the letter (1.0, experienced) and the merchant
    # hears of it (0.7, heard).
    # Plan-layer update (2026-11-11): result-driven step advancement
    # and the owner's probe candidates intentionally changed the
    # trajectory again — the canonical run is now the 87-event
    # rule_planlayer_seed42, with the same emergent chain (the chain
    # assertions below are unchanged). The 85-event branch remains as
    # the pre-upgrade baseline; see PLAN_LAYER_REPORT.md.
    import os
    import subprocess
    import sys
    driver = (
        "import asyncio, sys;"
        "from pathlib import Path;"
        "sys.path.insert(0, sys.argv[1]);"
        "from v02.runner import run_simulation;"
        "asyncio.run(run_simulation(mode='rule', ticks=20, seed=42,"
        " run_name='rule_det_test', out_root=Path(sys.argv[2])))"
    )
    logs, summaries = [], []
    for hash_seed in ("0", "1", "42"):
        out = tmp_path / f"hs{hash_seed}"
        env = dict(os.environ, PYTHONHASHSEED=hash_seed)
        subprocess.run([sys.executable, "-c", driver, str(ROOT), str(out)],
                       env=env, check=True, capture_output=True, text=True)
        run_dir = out / "rule_det_test"
        logs.append((run_dir / "world_log.json").read_text(encoding="utf-8"))
        summaries.append(json.loads((run_dir / "summary.json")
                                     .read_text(encoding="utf-8")))
    assert logs[0] == logs[1] == logs[2]
    s = summaries[0]
    assert s["total_events"] == 87   # plan-layer canonical (was 85 pre-upgrade)
    assert s["db_counts"]["dedup_drops"] == 0   # nothing to suppress in a clean run
    soldier = s["final_beliefs"]["soldier"]["anonymous_letter_exists"]
    assert soldier["confidence"] == 1.0 and soldier["kind"] == "experienced"
    merchant = s["final_beliefs"]["merchant"]["anonymous_letter_exists"]
    assert merchant["confidence"] == 0.7 and merchant["kind"] == "heard"
