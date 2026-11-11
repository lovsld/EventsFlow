"""Event-driven Scheduler (Phase B).

Structure: one asyncio task per agent. An agent task blocks on its
broker queue; the arrival of a perception envelope is the ONLY thing
that wakes it. On waking it interprets the perception privately, asks
its provider for a decision, and hands the resulting intent to the
SubmissionGate.

The tick survives purely as the experiment-control shell (design doc
section 4.6): the scheduler publishes one envelope batch per tick and
opens exactly one submission window per tick. Inside the window all
agents decide concurrently; when every agent has submitted, the gate
closes and the scheduler makes the SINGLE world commit of the tick
(WorldEngine.step). Agents have no reference to the world at all, and
the gate is the only path intents can take — there is no bypass.

Determinism note: each agent's provider draws randomness only from its
own seeded RNG, and each agent task processes its envelopes strictly in
tick order, so rule/mock runs reproduce Phase A results exactly.
"""
from __future__ import annotations

import asyncio


def intent_from_decision(decision) -> dict:
    """AgentDecision -> world intent dict (the Phase A conversion)."""
    if decision is None:
        return {"verb": "wait"}
    a = decision.action
    return {"verb": a.type, "target": a.target, "target_location": a.target_location,
            "object": a.object, "topic": a.topic or a.claim, "claim": a.claim,
            "text": a.content}


class SubmissionGate:
    """The unified submission entry. One window per tick; each known
    agent may submit exactly once per window; the collected intents are
    handed to the World Engine in one commit by the scheduler."""

    def __init__(self, agent_ids: list[str]):
        self.agent_ids = list(agent_ids)
        self._intents: dict[str, dict] = {}
        self._records: dict[str, dict] = {}
        self._open = False
        self._all_submitted: asyncio.Event | None = None

    def open_window(self) -> None:
        self._intents, self._records = {}, {}
        self._all_submitted = asyncio.Event()
        self._open = True

    def submit(self, agent_id: str, intent: dict, record: dict | None = None) -> None:
        if not self._open:
            raise RuntimeError("submission outside an open tick window")
        if agent_id not in self.agent_ids:
            raise ValueError(f"unknown agent {agent_id!r}")
        if agent_id in self._intents:
            raise RuntimeError(f"duplicate submission from {agent_id} in one tick")
        self._intents[agent_id] = intent
        if record is not None:
            self._records[agent_id] = record
        if len(self._intents) == len(self.agent_ids):
            self._all_submitted.set()

    async def wait_all(self) -> None:
        await self._all_submitted.wait()

    @property
    def all_submitted(self) -> bool:
        return self._all_submitted is not None and self._all_submitted.is_set()

    def close_and_collect(self) -> tuple[dict[str, dict], dict[str, dict]]:
        self._open = False
        return dict(self._intents), dict(self._records)


class Scheduler:
    def __init__(self, world, broker, agents: dict, store, agent_ids: list[str],
                 on_tick_end=None):
        self.world = world
        self.broker = broker
        self.agents = agents
        self.store = store
        self.agent_ids = list(agent_ids)
        self.gate = SubmissionGate(self.agent_ids)
        self._agent_errors: list[Exception] = []
        # Phase D: optional experiment hook, called after each world
        # commit (phase "post_step") and once after the final perception
        # pass (phase "final") with a per-agent subjective-state snapshot.
        self.on_tick_end = on_tick_end

    def _state_snapshot(self) -> dict:
        snap = {}
        for aid in self.agent_ids:
            st = self.agents[aid].state
            snap[aid] = {
                "location": self.world.state["agents"][aid]["location"],
                "beliefs": {b.key: {"confidence": b.confidence, "kind": b.kind}
                            for b in st.beliefs},
                "trust": {other: rel.get("trust", 0.3)
                          for other, rel in st.relationships.items()},
                "emotions": dict(st.emotions),
                "plan": (st.current_plan.goal if st.current_plan else None),
            }
        return snap

    def _emit_tick_end(self, tick: int, phase: str) -> None:
        if self.on_tick_end is not None:
            self.on_tick_end({"tick": tick, "phase": phase,
                              "agents": self._state_snapshot()})

    # ---------------- agent side ----------------
    async def _agent_loop(self, agent_id: str) -> None:
        agent = self.agents[agent_id]
        queue = self.broker.queues[agent_id]
        while True:
            envelope = await queue.get()
            try:
                perc = envelope["perception"]
                agent.receive(perc, envelope["action_result"])
                self._persist_chain_updates(agent)
                if envelope["final"]:
                    agent.memory.save_state()
                    return
                try:
                    decision, record = await agent.decide(perc)
                except Exception as e:  # never wedge the barrier; surface later
                    self._agent_errors.append(e)
                    decision, record = None, {
                        "character_id": agent_id, "tick": perc["tick"],
                        "provider": agent.provider.name, "model": agent.provider.model,
                        "prompt_version": "acp-v0.2.0", "context_summary": "",
                        "input_tokens": 0, "output_tokens": 0, "raw_output": "",
                        "parsed_output": None, "reasoning_summary": "",
                        "execution_result": None, "latency_ms": 0,
                        "error": f"scheduler: unexpected agent error: {e!r}"}
                self.gate.submit(agent_id, intent_from_decision(decision), record)
            finally:
                queue.task_done()

    # ---------------- Phase C chain persistence ----------------
    def _persist_chain_updates(self, agent) -> None:
        """Drain the cognitive-chain records an agent produced while
        interpreting a perception (prediction errors and everything they
        caused) into the audit store."""
        for upd in agent.drain_chain_updates():
            kind = upd["kind"]
            if kind == "prediction_error":
                self.store.update_prediction_error(
                    upd["prediction_id"], upd["error"], upd["tick"])
            elif kind == "belief_change":
                self.store.record_belief_change(upd)
            elif kind == "plan_transition":
                self.store.record_plan_transition(upd)
            elif kind == "consolidation":
                self.store.record_consolidation(upd)

    # ---------------- world side ----------------
    async def run(self, ticks: int) -> list[dict]:
        tasks = {aid: asyncio.create_task(self._agent_loop(aid))
                 for aid in self.agent_ids}
        tick_records: list[dict] = []
        deliveries_by_tick: dict[int, list[dict]] = {}
        last_events: list[dict] = []
        try:
            for _ in range(ticks):
                snap = self.world.snapshot()
                results = snap.get("last_action_results", {})
                records = self.broker.publish(snap, last_events)
                if last_events:
                    self.store.record_deliveries(records)
                    deliveries_by_tick[snap["tick"]] = records
                self._flush_dedup_drops()
                self._flush_counterfactual_suppressions()
                self.gate.open_window()
                self.broker.dispatch_all(results)
                await self.gate.wait_all()
                if self._agent_errors:
                    raise self._agent_errors[0]
                intents, decision_records = self.gate.close_and_collect()
                events = self.world.step(intents)   # the single commit point
                new_results = self.world.state["last_action_results"]
                for cid in self.agent_ids:
                    record = decision_records[cid]
                    record["execution_result"] = new_results.get(cid)
                    record.setdefault("decision_key", f"{cid}:{record.get('tick')}")
                    self.store.record_decision(record)
                    # Phase C: the decision's prediction and its plan
                    # transitions join the audit chain under the same key
                    if record.get("prediction"):
                        self.store.record_prediction(
                            record["prediction"], record["decision_key"])
                    for tr in record.get("plan_transitions", []):
                        self.store.record_plan_transition(
                            {**tr, "decision_key": record["decision_key"]})
                self.store.record_events(events)
                tick_records.append({
                    "tick": self.world.state["tick"], "time": self.world.state["time"],
                    "positions": {a: self.world.state["agents"][a]["location"]
                                  for a in self.agent_ids},
                    "events": events, "deliveries": []})
                self._emit_tick_end(self.world.state["tick"], "post_step")
                last_events = events
            # final perception pass: the last tick's events are delivered
            # and interpreted too (no further decisions), as in Phase A
            snap = self.world.snapshot()
            results = snap.get("last_action_results", {})
            records = self.broker.publish(snap, last_events)
            if last_events:
                self.store.record_deliveries(records)
                deliveries_by_tick[snap["tick"]] = records
            self._flush_dedup_drops()
            self._flush_counterfactual_suppressions()
            self.broker.dispatch_all(results, final=True)
            await asyncio.gather(*tasks.values())
            self._emit_tick_end(self.world.state["tick"], "final")
        finally:
            for t in tasks.values():
                if not t.done():
                    t.cancel()
        for rec in tick_records:
            rec["deliveries"] = deliveries_by_tick.get(rec["tick"], [])
        return tick_records

    def _flush_dedup_drops(self) -> None:
        drops = self.broker.drain_dedup_drops()
        if drops:
            self.store.record_dedup_drops(drops)

    def _flush_counterfactual_suppressions(self) -> None:
        recs = self.broker.drain_counterfactual_suppressions()
        if recs:
            self.store.record_counterfactual_suppressions(recs)
