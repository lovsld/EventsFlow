"""Event Broker.

The broker is the ONLY channel through which objective events reach agents.
After the World Engine commits a tick, the broker files each event into the
per-agent delivery list strictly according to the Perception Engine's rules
(same room = full event; adjacent room = muffled notice for speech only;
elsewhere = nothing). It writes an auditable delivery record so tests can
prove information isolation.
"""
from __future__ import annotations

from .perception import build_perception


def deliver(world: dict, committed_events: list[dict], agent_ids: list[str]) -> tuple[dict, list[dict]]:
    """Returns (perceptions_by_agent, delivery_records)."""
    perceptions, records = {}, []
    for aid in agent_ids:
        perc = build_perception(aid, world, committed_events)
        perceptions[aid] = perc
        heard_ids = [e["event_id"] for e in perc["events"]]
        records.append({"agent_id": aid, "tick": world["tick"],
                        "delivered_event_ids": heard_ids,
                        "muffled_event_ids": [m["event_id"] for m in perc["muffled"]]})
    return perceptions, records
