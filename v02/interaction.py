"""Interaction Manager (Phase B) — session lifecycle as a first-class
component, split out of world.py.

A session exists wherever >= 2 agents are co-located. Its lifecycle is
modelled explicitly and kept in the session record (plus `history` for
closed sessions):

  started  — a co-location group forms a session
  joined   — an agent appears in a session whose predecessor at the same
             location (closed in the same sync) did not contain them
  left     — a participant is no longer at the session's location when
             the session closes
  listen   — a participant takes a listener stance in the session
  interrupt— an utterance lands while the floor is already taken
  ended    — the co-location group dissolves or changes membership

The world-visible events are exactly the ones V0.1/Phase A produced
(session_start / session_end on membership change); the lifecycle log is
manager-side audit detail and changes no world semantics.

The Resolver consults this manager for interaction-class actions:
  - take_floor() decides the interruption flag for speech (the floor is
    per location per tick: the first speaker holds it, later speakers in
    the same tick interrupt — the V0.1 rule, unchanged);
  - register_listener() records a listen action as a stance in the
    session at the agent's location. Listeners are participants: the
    room perception rule already delivers full session utterances to
    them. The stance is cleared when the agent leaves the location.
"""
from __future__ import annotations


class InteractionManager:
    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.history: list[dict] = []
        self._counter = 0
        self._floor: dict[str, str] = {}
        self._floor_tick = -1
        self._listen_stance: dict[str, str] = {}   # agent_id -> location

    # ---------------- floor / interruption (Resolver-facing) ----------------
    def begin_tick(self, tick: int) -> None:
        if tick != self._floor_tick:
            self._floor = {}
            self._floor_tick = tick

    def take_floor(self, location: str, speaker: str) -> bool:
        """Return True when this speech interrupts: the location's floor
        was already taken this tick (the V0.1 spoken-in-session rule)."""
        if location in self._floor:
            return True
        self._floor[location] = speaker
        return False

    # ---------------- listeners (Resolver-facing) ----------------
    def register_listener(self, world: dict, agent_id: str) -> None:
        loc = world["agents"][agent_id]["location"]
        self._listen_stance[agent_id] = loc
        session = self.active_session_at(loc)
        if session is not None and agent_id in session["participants"]:
            if agent_id not in session["listeners"]:
                session["listeners"].append(agent_id)
                session["listeners"].sort()
            session["lifecycle"].append(
                {"kind": "listen", "agent": agent_id, "tick": world["tick"]})

    def is_listener(self, session: dict, agent_id: str) -> bool:
        return agent_id in session.get("listeners", [])

    # ---------------- queries ----------------
    def active_session_at(self, location: str) -> dict | None:
        for s in self.sessions.values():
            if s["location"] == location:
                return s
        return None

    def session_of(self, agent_id: str) -> dict | None:
        for s in self.sessions.values():
            if agent_id in s["participants"]:
                return s
        return None

    # ---------------- lifecycle sync (World Engine-facing) ----------------
    def sync(self, world: dict, tick: int) -> list[dict]:
        events = []
        by_loc: dict[str, list[str]] = {}
        for aid, a in world["agents"].items():
            by_loc.setdefault(a["location"], []).append(aid)

        # drop listen stances of agents who are no longer at that location
        for aid, loc in list(self._listen_stance.items()):
            if world["agents"][aid]["location"] != loc:
                del self._listen_stance[aid]

        active_keys = set()
        for loc, members in by_loc.items():
            if len(members) < 2:
                continue
            active_keys.add(f"{loc}:{'/'.join(sorted(members))}")

        # close sessions whose group dissolved or changed membership
        predecessors: dict[str, dict] = {}   # location -> closed session
        closed = []
        for key in list(self.sessions):
            if key not in active_keys:
                s = self.sessions.pop(key)
                loc = s["location"]
                predecessors[loc] = s
                present = set(by_loc.get(loc, []))
                for aid in s["participants"]:
                    if aid not in present:
                        s["lifecycle"].append(
                            {"kind": "left", "agent": aid, "tick": tick})
                s["lifecycle"].append({"kind": "ended", "tick": tick})
                s["ended_tick"] = tick
                self.history.append(s)
                closed.append(s)

        # open sessions for groups that have none yet
        opened = []
        for loc, members in by_loc.items():
            if len(members) < 2:
                continue
            key = f"{loc}:{'/'.join(sorted(members))}"
            if key in self.sessions:
                continue
            self._counter += 1
            sid = f"S{self._counter}"
            lifecycle = [{"kind": "started", "tick": tick,
                          "participants": sorted(members)}]
            pred = predecessors.get(loc)
            if pred is not None:
                for aid in sorted(members):
                    if aid not in pred["participants"]:
                        lifecycle.append({"kind": "joined", "agent": aid, "tick": tick})
            session = {"session_id": sid, "location": loc,
                       "participants": sorted(members),
                       "started_tick": tick, "transcript": [],
                       "listeners": [], "lifecycle": lifecycle}
            self.sessions[key] = session
            opened.append(session)

        # world-visible order matches V0.1/Phase A: starts first, then ends
        for s in opened:
            events.append({"type": "session_start", "session": s["session_id"],
                           "location": s["location"], "actors": s["participants"]})
        for s in closed:
            events.append({"type": "session_end", "session": s["session_id"],
                           "location": s["location"], "actors": s["participants"]})

        # refresh listener lists of continuing/new sessions from stances
        for s in self.sessions.values():
            s["listeners"] = sorted(
                aid for aid, loc in self._listen_stance.items()
                if loc == s["location"] and aid in s["participants"])
        return events

    def record_utterance(self, world: dict, location: str, event: dict,
                         interrupted: bool) -> None:
        for s in self.sessions.values():
            if s["location"] == location and event["actors"][0] in s["participants"]:
                s["transcript"].append({"tick": world["tick"], "event_id": event["event_id"],
                                        "speaker": event["actors"][0],
                                        "kind": event["type"], "topic": event.get("topic"),
                                        "interrupted": interrupted})
                if interrupted:
                    s["lifecycle"].append(
                        {"kind": "interrupt", "agent": event["actors"][0],
                         "tick": world["tick"], "event_id": event["event_id"]})
