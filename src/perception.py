"""Perception Engine.

Given the objective world snapshot, build what ONE agent can actually
perceive this tick:

- its own location and co-located agents (public appearance only: id/name/role)
- events that happened at its location last tick (speech it could hear,
  movements it could see, object events in the same room)
- utterances addressed to it or spoken in the same room

Agents in another room receive NONE of it. There is no global broadcast.
Adjacent-room rule (tavern layout): 'door' is adjacent to 'tables' and
'outside'; muffled speech topics (not content) may leak between adjacent
rooms, marked kind='overheard_muffled' — hearers learn THAT a conversation
happened, not what was said.
"""
from __future__ import annotations

ADJACENCY = {
    "bar": {"tables"},
    "tables": {"bar", "door", "back_room"},
    "door": {"tables", "outside"},
    "back_room": {"tables"},
    "outside": {"door"},
}
LOCATIONS = ["bar", "tables", "door", "back_room", "outside"]


def build_perception(agent_id: str, world: dict, last_events: list[dict]) -> dict:
    loc = world["agents"][agent_id]["location"]
    colocated = [
        {"id": oid, "name": o["name"], "role": o["role"]}
        for oid, o in world["agents"].items()
        if oid != agent_id and o["location"] == loc
    ]
    perceived, muffled = [], []
    for ev in last_events:
        ev_loc = ev.get("location")
        if ev_loc == loc:
            perceived.append(ev)
        elif ev_loc in ADJACENCY.get(loc, set()) and ev["type"] in ("speak", "ask", "confront"):
            muffled.append({"event_id": ev["event_id"], "type": "muffled_voices",
                             "location": ev_loc, "actors": ev.get("actors", [])})
    return {
        "agent_id": agent_id,
        "tick": world["tick"],
        "time": world["time"],
        "location": loc,
        "colocated": colocated,
        "visible_objects": list(world["locations"][loc].get("objects", [])),
        "events": perceived,
        "muffled": muffled,
        "world_public": {"weather": world.get("weather", "clear")},
    }
