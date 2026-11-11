"""Per-tick checkpoints — the shared mechanism behind --resume and
--loop (local runtime trio, see LOCAL_DEPLOY.md).

After every world commit the runner serializes EVERYTHING a fresh
process needs to continue the run indistinguishably into
<run_dir>/checkpoint.json:

  world      — the full objective state dict (tick / time / positions /
               objects & the letter / last_action_results), the world
               RNG state, the event sequence counter, and the
               Interaction Manager's sessions / history / floor /
               listener stances;
  agents     — each agent's subjective CharacterState (beliefs, trust,
               emotions, plan, short/long-term memory, ...), its
               in-flight pending prediction, pending step check, last
               prediction error, consolidation cursor, drained-at-rest
               chain buffers, and its provider's RNG state;
  broker     — the (agent, event) delivered set behind dedup;
  scheduler  — tick_records so far, the deliveries-by-tick map and the
               last committed events (the next tick's perception input);
  plans      — the global plan-id sequence, so resumed plan ids never
               collide with pre-interruption ones.

Timing invariant the whole design rests on: the checkpoint is written
at the END of a scheduler iteration — after the world commit and its
audit rows, but BEFORE the next iteration's publish (and, for a
finished run, before the final perception pass). Delivery bookkeeping
in the checkpoint therefore never includes the last committed tick's
events, and Store.rollback_after(checkpoint tick) deletes exactly the
rows a replay will rewrite. That is what makes an interrupted +
resumed rule run byte-identical to an uninterrupted one.

world_log.json is also rewritten here, incrementally per tick (same
content the runner writes at the end), so a killed run's log is
complete up to its last committed tick.
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

from .plan_manager import plan_seq, restore_plan_seq
from .protocol import CharacterState, Prediction, PredictionError

CHECKPOINT_FILE = "checkpoint.json"
META_FILE = "run_meta.json"


def rng_state_to_json(rng: random.Random) -> list:
    version, internal, gauss = rng.getstate()
    return [version, list(internal), gauss]


def rng_state_from_json(data) -> tuple:
    version, internal, gauss = data
    return (int(version), tuple(int(x) for x in internal), gauss)


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class RunCheckpointer:
    """Owns checkpoint.json + the incremental world_log.json of one run.
    Bound to the run's live world / agents / broker; the scheduler
    calls write() after every commit and flush_pause() when --loop
    pauses on quota."""

    def __init__(self, run_dir: Path, world, agents: dict, broker):
        self.run_dir = Path(run_dir)
        self.world = world
        self.agents = agents
        self.broker = broker
        self._payload_text: str | None = None

    # ---------------- serialization ----------------
    def build_payload(self, tick_records: list[dict],
                      deliveries_by_tick: dict, last_events: list[dict]) -> dict:
        world = self.world
        agents_payload = {}
        for cid, agent in self.agents.items():
            rng = getattr(agent.provider, "rng", None)
            agents_payload[cid] = {
                "state": agent.state.model_dump(mode="json"),
                "pending_prediction": (
                    agent.pending_prediction.model_dump(mode="json")
                    if agent.pending_prediction is not None else None),
                "pending_step_check": agent._pending_step_check,
                "last_error": (
                    agent.last_error.model_dump(mode="json")
                    if agent.last_error is not None else None),
                "cons_from": agent._cons_from,
                "tick": getattr(agent, "_tick", 0),
                "chain_updates": agent._chain_updates,
                "plan_transitions_buffer": agent.plan_manager.transitions,
                "provider_rng": (rng_state_to_json(rng)
                                 if isinstance(rng, random.Random) else None),
            }
        im = world.interactions
        return {
            "version": 1,
            "world_tick": world.state["tick"],
            "world_state": world.state,
            "world_rng": rng_state_to_json(world.rng),
            "event_seq": world.event_seq[0],
            "interactions": {
                "sessions": im.sessions,
                "history": im.history,
                "counter": im._counter,
                "floor": im._floor,
                "floor_tick": im._floor_tick,
                "listen_stance": im._listen_stance,
            },
            "broker_delivered": sorted(
                [list(pair) for pair in self.broker._delivered]),
            "plan_seq": plan_seq(),
            "agents": agents_payload,
            "tick_records": tick_records,
            "deliveries_by_tick": {str(t): d
                                   for t, d in deliveries_by_tick.items()},
            "last_events": last_events,
        }

    def write(self, tick_records: list[dict], deliveries_by_tick: dict,
              last_events: list[dict]) -> None:
        """Scheduler on_tick_committed hook: persist the checkpoint for
        the just-committed world state and refresh world_log.json."""
        payload = self.build_payload(tick_records, deliveries_by_tick,
                                     last_events)
        self._payload_text = json.dumps(payload, ensure_ascii=False)
        _atomic_write_text(self.run_dir / CHECKPOINT_FILE, self._payload_text)
        self.write_world_log(tick_records, deliveries_by_tick)

    def flush_pause(self, tick_records: list[dict], deliveries_by_tick: dict,
                    last_events: list[dict]) -> None:
        """--loop quota pause: the checkpoint is already on disk from
        the last commit (rewrite it verbatim for safety) and world_log
        gains the latest delivery fill — 'save the checkpoint first,
        then wait'."""
        if self._payload_text is not None:
            _atomic_write_text(self.run_dir / CHECKPOINT_FILE,
                               self._payload_text)
        self.write_world_log(tick_records, deliveries_by_tick)

    def write_world_log(self, tick_records: list[dict],
                        deliveries_by_tick: dict) -> None:
        records = []
        for rec in tick_records:
            out = dict(rec)
            out["deliveries"] = deliveries_by_tick.get(rec["tick"], [])
            records.append(out)
        _atomic_write_text(
            self.run_dir / "world_log.json",
            json.dumps(records, ensure_ascii=False, indent=1))

    # ---------------- restore ----------------
    def restore(self, payload: dict) -> None:
        """Load a checkpoint payload into this checkpointer's (freshly
        constructed) world / agents / broker, replacing their initial
        state wholesale. Callers roll the audit store back with
        Store.rollback_after(payload["world_tick"]) separately."""
        world = self.world
        world.state = payload["world_state"]
        world.tick_no = payload["world_tick"]
        world.rng.setstate(rng_state_from_json(payload["world_rng"]))
        world.event_seq = [int(payload["event_seq"])]
        im = world.interactions
        saved = payload["interactions"]
        im.sessions = saved["sessions"]
        im.history = saved["history"]
        im._counter = saved["counter"]
        im._floor = saved["floor"]
        im._floor_tick = saved["floor_tick"]
        im._listen_stance = saved["listen_stance"]
        self.broker._delivered = {tuple(pair)
                                  for pair in payload["broker_delivered"]}
        restore_plan_seq(payload["plan_seq"])
        for cid, ap in payload["agents"].items():
            agent = self.agents[cid]
            agent.state = CharacterState.model_validate(ap["state"])
            agent.memory.state = agent.state
            agent.pending_prediction = (
                Prediction.model_validate(ap["pending_prediction"])
                if ap["pending_prediction"] is not None else None)
            agent._pending_step_check = ap["pending_step_check"]
            agent.last_error = (
                PredictionError.model_validate(ap["last_error"])
                if ap["last_error"] is not None else None)
            agent._cons_from = ap["cons_from"]
            agent._tick = ap["tick"]
            agent._chain_updates = list(ap["chain_updates"])
            agent.plan_manager.transitions = list(
                ap["plan_transitions_buffer"])
            rng = getattr(agent.provider, "rng", None)
            if isinstance(rng, random.Random) and ap["provider_rng"] is not None:
                rng.setstate(rng_state_from_json(ap["provider_rng"]))
            agent.memory.resync_files()


def load_checkpoint(run_dir: Path) -> dict | None:
    path = Path(run_dir) / CHECKPOINT_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
