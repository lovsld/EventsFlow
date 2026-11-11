"""Interaction Session manager.

When >=2 agents share a location, they are in an Interaction Session.
Sessions have ids, participants, a transcript (objective record) and support:
interrupt (a speak intent that lands while an exchange is ongoing),
refusal (a 'refuse' intent is itself an event in the transcript),
leaving (a move intent by a participant ends their participation).

The manager is world-side bookkeeping only: it records what objectively
happened; it never tells any agent what to say.
"""
from __future__ import annotations


class InteractionManager:
    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self._counter = 0

    def sync(self, world: dict, tick: int) -> list[dict]:
        """Recompute sessions from co-location. Returns session lifecycle events."""
        events = []
        by_loc: dict[str, list[str]] = {}
        for aid, a in world["agents"].items():
            by_loc.setdefault(a["location"], []).append(aid)
        active_keys = set()
        for loc, members in by_loc.items():
            if len(members) < 2:
                continue
            key = f"{loc}:{'/'.join(sorted(members))}"
            active_keys.add(key)
            if key not in self.sessions:
                self._counter += 1
                sid = f"S{self._counter}"
                self.sessions[key] = {"session_id": sid, "location": loc,
                                      "participants": sorted(members), "started_tick": tick,
                                      "transcript": []}
                events.append({"type": "session_start", "session": sid, "location": loc,
                               "actors": sorted(members)})
            world["agents"][members[0]].setdefault("_noop", None)  # no-op clarity anchor
        for key in list(self.sessions):
            if key not in active_keys:
                s = self.sessions.pop(key)
                events.append({"type": "session_end", "session": s["session_id"],
                               "location": s["location"], "actors": s["participants"]})
        return events

    def record_utterance(self, world: dict, location: str, event: dict, interrupted: bool) -> None:
        for key, s in self.sessions.items():
            if s["location"] == location and event["actors"][0] in s["participants"]:
                s["transcript"].append({"tick": world["tick"], "event_id": event["event_id"],
                                        "speaker": event["actors"][0],
                                        "kind": event["type"], "topic": event.get("topic"),
                                        "interrupted": interrupted})
