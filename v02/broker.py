"""Event Broker (Phase B) — perception, deduplicated delivery, queues.

The broker is the only path from committed world events to agents:

  publish(snapshot, events)  — compute every agent's perception of the
      committed events under the room/muffled perception rules (same
      rules as V0.1/Phase A), drop anything that was already delivered
      to that agent, and return the audit records for the store.
  dispatch(results, final)   — wrap the published perceptions into
      envelopes and put them on the agents' asyncio queues; an envelope
      on the queue is what wakes an agent (see scheduler.py).

Deduplication: an (agent_id, event_id) pair — full or muffled — is
delivered at most once per run. A repeat is not delivered again; it is
recorded in `dedup_drops` (and persisted to the dedup_drops table by the
runner) with the reason, so duplicate-suppression is itself auditable.

Information isolation is unchanged: same room = full event, adjacent
room speech = muffled existence notice only, elsewhere = nothing.

Counterfactual suppression (Phase D): an EventBroker may carry
suppression rules — {"recipient": <agent>, optionally "actor",
"claim", "topic", "type"} — for experiments. A delivery (full or
muffled) matching a rule for its recipient is never placed in that
agent's perception; the suppression is recorded in
`counterfactual_suppressions` (persisted to the table of the same
name), separate from dedup_drops: the objective event still happened
and other recipients still receive it. This is how a counterfactual
pair is made to differ by exactly one delivery.
"""
from __future__ import annotations

import asyncio

from .world_config import adjacency_of


def build_perception(agent_id: str, world: dict, last_events: list[dict],
                     adjacency: dict | None = None) -> dict:
    """The perception rules, verbatim from V0.1/Phase A. V0.2-B: the
    muffled-adjacency graph comes from the run's Scenario Definition
    instance (the broker passes its own); the default is the tavern
    graph for direct callers."""
    if adjacency is None:
        adjacency = adjacency_of()
    loc = world["agents"][agent_id]["location"]
    colocated = [{"id": oid, "name": o["name"], "role": o["role"]}
                 for oid, o in world["agents"].items()
                 if oid != agent_id and o["location"] == loc]
    perceived, muffled = [], []
    for ev in last_events:
        ev_loc = ev.get("location")
        if ev_loc == loc:
            perceived.append(ev)
        elif ev_loc in adjacency.get(loc, set()) and ev["type"] in ("speak", "ask", "confront"):
            muffled.append({"event_id": ev["event_id"], "type": "muffled_voices",
                            "location": ev_loc, "actors": ev.get("actors", [])})
    return {"agent_id": agent_id, "tick": world["tick"], "time": world["time"],
            "location": loc, "colocated": colocated,
            "visible_objects": list(world["locations"][loc].get("objects", [])),
            "events": perceived, "muffled": muffled,
            "world_public": {"weather": world.get("weather", "clear")}}


def deliver(world: dict, committed_events: list[dict], agent_ids: list[str]):
    """Phase A-compatible one-shot delivery (perceptions, records).
    Used by tests and any caller that does not run the scheduler."""
    broker = EventBroker(agent_ids)
    records = broker.publish(world, committed_events)
    return broker.pending_perceptions(), records


def _as_list(value) -> list:
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def match_suppression(rule: dict, agent_id: str, event: dict) -> bool:
    """True when `event`'s delivery to `agent_id` is suppressed by
    `rule`. Every field present in the rule must match; "claim"/"topic"
    may name one value or a list of equivalent values (a claim class)."""
    if rule.get("recipient") != agent_id:
        return False
    if "actor" in rule:
        actors = event.get("actors") or []
        if rule["actor"] not in actors:
            return False
    for field in ("claim", "topic", "type"):
        if field in rule and event.get(field) not in _as_list(rule[field]):
            return False
    return True


class EventBroker:
    def __init__(self, agent_ids: list[str],
                 suppression_rules: list[dict] | None = None,
                 scenario=None):
        self.agent_ids = list(agent_ids)
        self.queues: dict[str, asyncio.Queue] = {aid: asyncio.Queue()
                                                 for aid in self.agent_ids}
        self._delivered: set[tuple[str, str]] = set()
        self.dedup_drops: list[dict] = []
        self.suppression_rules = list(suppression_rules or [])
        self.counterfactual_suppressions: list[dict] = []
        self._pending: dict[str, dict] = {}
        # V0.2-B threading: the run's Scenario Definition instance;
        # its adjacency drives the muffled-perception rule.
        self.scenario = scenario
        self._adjacency = scenario.adjacency_sets() if scenario else None

    # ---------------- publish: perception + dedup + audit ----------------
    def publish(self, world_snapshot: dict, events: list[dict]) -> list[dict]:
        records = []
        by_id = {e["event_id"]: e for e in events}
        for aid in self.agent_ids:
            perc = build_perception(aid, world_snapshot, events,
                                    adjacency=self._adjacency)
            perc = self._suppress(aid, perc, by_id, tick=world_snapshot["tick"])
            perc = self._dedup(aid, perc, tick=world_snapshot["tick"])
            self._pending[aid] = perc
            records.append({"agent_id": aid, "tick": world_snapshot["tick"],
                            "delivered_event_ids": [e["event_id"] for e in perc["events"]],
                            "muffled_event_ids": [m["event_id"] for m in perc["muffled"]]})
        return records

    # ---------------- counterfactual suppression (Phase D) ----------------
    def _suppress(self, agent_id: str, perc: dict, by_id: dict,
                  tick: int) -> dict:
        """Remove rule-matched deliveries from ONE agent's perception
        and audit them. Runs before dedup: a suppressed event is not
        'already delivered', it is never delivered. The committed event
        objects themselves are not touched."""
        if not self.suppression_rules:
            return perc
        kept_events, kept_muffled = [], []
        for ev in perc["events"]:
            rule = self._matching_rule(agent_id, ev)
            if rule is None:
                kept_events.append(ev)
            else:
                self.counterfactual_suppressions.append(
                    {"tick": tick, "agent_id": agent_id,
                     "event_id": ev["event_id"], "kind": "full", "rule": rule})
        for mf in perc["muffled"]:
            original = by_id.get(mf["event_id"])
            rule = self._matching_rule(agent_id, original) if original else None
            if rule is None:
                kept_muffled.append(mf)
            else:
                self.counterfactual_suppressions.append(
                    {"tick": tick, "agent_id": agent_id,
                     "event_id": mf["event_id"], "kind": "muffled", "rule": rule})
        return dict(perc, events=kept_events, muffled=kept_muffled)

    def _matching_rule(self, agent_id: str, event: dict) -> dict | None:
        for rule in self.suppression_rules:
            if match_suppression(rule, agent_id, event):
                return rule
        return None

    def drain_counterfactual_suppressions(self) -> list[dict]:
        recs, self.counterfactual_suppressions = \
            self.counterfactual_suppressions, []
        return recs

    def _dedup(self, agent_id: str, perc: dict, tick: int) -> dict:
        kept_events, kept_muffled = [], []
        for ev in perc["events"]:
            if self._mark_or_drop(agent_id, ev["event_id"], "full", tick):
                kept_events.append(ev)
        for mf in perc["muffled"]:
            if self._mark_or_drop(agent_id, mf["event_id"], "muffled", tick):
                kept_muffled.append(mf)
        return dict(perc, events=kept_events, muffled=kept_muffled)

    def _mark_or_drop(self, agent_id: str, event_id: str, kind: str,
                      tick: int) -> bool:
        key = (agent_id, event_id)
        if key in self._delivered:
            self.dedup_drops.append(
                {"tick": tick, "agent_id": agent_id, "event_id": event_id,
                 "kind": kind, "reason": "already_delivered"})
            return False
        self._delivered.add(key)
        return True

    def drain_dedup_drops(self) -> list[dict]:
        drops, self.dedup_drops = self.dedup_drops, []
        return drops

    def pending_perceptions(self) -> dict[str, dict]:
        return dict(self._pending)

    # ---------------- dispatch: wake the agents ----------------
    def dispatch(self, agent_id: str, action_result: dict | None,
                 final: bool = False) -> None:
        perc = self._pending.pop(agent_id)
        self.queues[agent_id].put_nowait(
            {"perception": perc, "action_result": action_result, "final": final})

    def dispatch_all(self, results: dict, final: bool = False) -> None:
        for aid in self.agent_ids:
            self.dispatch(aid, results.get(aid), final=final)
