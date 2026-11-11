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
import os
import time


class QuotaStopRun(Exception):
    """Internal control signal: --loop's total quota-wait budget is
    exhausted, so the run stops BEFORE the blocked tick commits. The
    runner turns this into a normal exit with quota_stopped in the
    summary; the run is resumable with --resume."""


class QuotaWaiter:
    """Shared --loop wait budget (one per run, shared by all agents).

    wait() sleeps ACP_QUOTA_SLEEP_MINUTES (default 20) and returns
    True while the TOTAL elapsed waiting time stays within
    ACP_QUOTA_MAX_WAIT_HOURS (default 12); past the budget it returns
    False immediately. Elapsed time is wall-clock (monotonic) across
    all agents, so three agents pausing together consume the budget
    once, not three times. `sleep_fn` is a test seam."""

    def __init__(self, sleep_fn=None):
        self.sleep_seconds = max(
            0.0, float(os.environ.get("ACP_QUOTA_SLEEP_MINUTES", "20")) * 60.0)
        self.max_wait_seconds = max(
            0.0, float(os.environ.get("ACP_QUOTA_MAX_WAIT_HOURS", "12")) * 3600.0)
        self.waits = 0
        self._started: float | None = None
        self._sleep_fn = sleep_fn

    async def wait(self) -> bool:
        if self._started is None:
            self._started = time.monotonic()
        if time.monotonic() - self._started >= self.max_wait_seconds:
            return False
        if self._sleep_fn is not None:
            await self._sleep_fn(self.sleep_seconds)
        else:
            await asyncio.sleep(self.sleep_seconds)
        self.waits += 1
        return True


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
                 on_tick_end=None, on_tick_committed=None, on_quota_pause=None,
                 quota_waiter: QuotaWaiter | None = None,
                 initial_tick_records: list[dict] | None = None,
                 initial_deliveries_by_tick: dict | None = None,
                 initial_last_events: list[dict] | None = None):
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
        # Local runtime trio (checkpoints / --loop): on_tick_committed
        # fires after every world commit with the live
        # (tick_records, deliveries_by_tick, last_events); on_quota_pause
        # fires before a quota wait with the same triple. Both default
        # to None = pre-trio behaviour.
        self.on_tick_committed = on_tick_committed
        self.on_quota_pause = on_quota_pause
        self.quota_waiter = quota_waiter
        self.quota_stopped = False
        # --resume: bookkeeping restored from the checkpoint, so the
        # returned tick_records cover the whole run, not just this
        # invocation's ticks.
        self.initial_tick_records = initial_tick_records or []
        self.initial_deliveries_by_tick = initial_deliveries_by_tick or {}
        self.initial_last_events = initial_last_events or []
        self._live: dict = {"tick_records": [], "deliveries_by_tick": {},
                            "last_events": []}

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
                decision, record = await self._decide_with_quota_retry(
                    agent, agent_id, perc)
                self.gate.submit(agent_id, intent_from_decision(decision), record)
            finally:
                queue.task_done()

    async def _decide_with_quota_retry(self, agent, agent_id: str, perc: dict):
        """agent.decide() with the --loop quota protocol on top.

        Without a quota waiter this is exactly the historical behaviour
        (a failed decision degrades to a wait and the tick commits).
        With one (--loop), a decision that failed on the structured
        QuotaExhaustedError signal — record["quota_exhausted"], set by
        the agent from the exception TYPE — does NOT stand: the
        checkpoint is flushed first, the waiter sleeps, and the SAME
        perception is decided again, so the tick is never burned on a
        degraded wait and the failed attempt leaves no audit rows.
        Plan transitions a failed attempt already produced (per-tick
        reconcile housekeeping) are carried into the successful
        attempt's record, keeping the audit trail identical to an
        uninterrupted run. Non-quota failures never enter the retry:
        they keep the degrade-to-wait semantics. If the wait budget
        runs out, a QuotaStopRun error makes run() stop the run BEFORE
        the tick commits; the checkpoint on disk is the resume point."""
        carried: list[dict] = []
        while True:
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
            if decision is not None or self.quota_waiter is None \
                    or not record.get("quota_exhausted"):
                break
            carried.extend(record.get("plan_transitions") or [])
            if self.on_quota_pause is not None:
                self.on_quota_pause(self._live["tick_records"],
                                    self._live["deliveries_by_tick"],
                                    self._live["last_events"])
            if await self.quota_waiter.wait():
                continue
            self._agent_errors.append(QuotaStopRun(
                "quota wait budget exhausted "
                f"({self.quota_waiter.max_wait_seconds / 3600:g}h total); "
                "run stopped before committing the blocked tick"))
            break
        if carried:
            record["plan_transitions"] = carried + list(
                record.get("plan_transitions") or [])
        return decision, record

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
            elif kind == "goal_change":
                self.store.record_goal_change(upd)
            elif kind == "consolidation":
                self.store.record_consolidation(upd)

    # ---------------- world side ----------------
    async def run(self, ticks: int) -> list[dict]:
        tasks = {aid: asyncio.create_task(self._agent_loop(aid))
                 for aid in self.agent_ids}
        tick_records: list[dict] = list(self.initial_tick_records)
        deliveries_by_tick: dict[int, list[dict]] = dict(
            self.initial_deliveries_by_tick)
        last_events: list[dict] = list(self.initial_last_events)
        self._live = {"tick_records": tick_records,
                      "deliveries_by_tick": deliveries_by_tick,
                      "last_events": last_events}
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
                    if any(isinstance(e, QuotaStopRun)
                           for e in self._agent_errors):
                        # --loop budget exhausted: stop gracefully with
                        # the blocked tick uncommitted (its decisions
                        # were never recorded); the checkpoint on disk
                        # is the --resume point.
                        self.quota_stopped = True
                        break
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
                    for gc in record.get("goal_changes", []):
                        self.store.record_goal_change(
                            {**gc, "decision_key": record["decision_key"]})
                self.store.record_events(events)
                tick_records.append({
                    "tick": self.world.state["tick"], "time": self.world.state["time"],
                    "positions": {a: self.world.state["agents"][a]["location"]
                                  for a in self.agent_ids},
                    "events": events, "deliveries": []})
                self._emit_tick_end(self.world.state["tick"], "post_step")
                last_events = events
                self._live["last_events"] = last_events
                if self.on_tick_committed is not None:
                    self.on_tick_committed(tick_records, deliveries_by_tick,
                                           last_events)
            if not self.quota_stopped:
                # final perception pass: the last tick's events are
                # delivered and interpreted too (no further decisions),
                # as in Phase A. Skipped on a quota stop: the blocked
                # tick never committed, so there is nothing new to
                # deliver, and the agents' final interpretation belongs
                # to the eventual completed run.
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
