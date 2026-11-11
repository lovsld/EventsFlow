"""World Engine for V0.2 — a direct port of the verified V0.1 world layer
(world_engine + action_resolver) with V0.2 character ids
(owner/merchant/soldier). The rules are unchanged:

- the World Engine is the only committer of objective state, and its
  step() is called only by the Scheduler with gate-collected intents;
- initiative = impulsiveness + world-RNG jitter; a second speaker in the
  same room/tick is flagged as an interruption (the floor is tracked by
  the Interaction Manager, which the Resolver consults);
- speech fails if the target already moved away this tick;
- search finds the letter only if it is objectively in the back room.

Phase B split: perception/delivery live in broker.py, session lifecycle
in interaction.py, driving/scheduling in scheduler.py. This module keeps
the WorldEngine and the Action Resolver. build_perception / deliver /
InteractionManager are re-exported for Phase A compatibility.
"""
from __future__ import annotations

import copy
import random

from .broker import build_perception, deliver
from .interaction import InteractionManager

__all__ = ["WorldEngine", "resolve", "InteractionManager",
           "build_perception", "deliver", "ADJACENCY", "LOCATIONS", "VERBS"]

ADJACENCY = {
    "bar": {"tables"}, "tables": {"bar", "door", "back_room"},
    "door": {"tables", "outside"}, "back_room": {"tables"}, "outside": {"door"},
}
LOCATIONS = ["bar", "tables", "door", "back_room", "outside"]
VERBS = {"move", "speak", "ask", "confront", "observe", "listen", "work",
         "search", "refuse", "wait", "give", "show", "take"}

AMBIENT = [
    ("rain_starts", None, "窗外下起雨来，酒馆里的人今晚大概走不了太早。"),
    ("bell_rings", "door", "酒馆门口的铃铛响了一声，又没有人进来。"),
    ("candle_flickers", "bar", "吧台的蜡烛晃了晃，老板的目光跟着晃了一下。"),
    ("distant_shout", "outside", "街上远处有人喊了一嗓子，很快没了声音。"),
    ("cat_knocks_glass", "tables", "一只野猫碰倒了桌边的空杯子，碎在地上。"),
]


def resolve(world: dict, intents: dict[str, dict], rng, event_seq: list[int],
            interactions: InteractionManager) -> list[dict]:
    def new_event(type_, actors, location, **kw):
        event_seq[0] += 1
        ev = {"event_id": f"E{event_seq[0]:04d}", "tick": world["tick"], "type": type_,
              "actors": actors, "location": location}
        ev.update(kw)
        return ev

    events: list[dict] = []
    order = sorted(intents.items(),
                   key=lambda kv: -(world["agents"][kv[0]]["initiative"] + rng.random() * 0.2))
    interactions.begin_tick(world["tick"])
    results: dict[str, dict] = {}

    for aid, intent in order:
        verb = intent.get("verb")
        me = world["agents"][aid]
        loc = me["location"]
        if verb not in VERBS:
            results[aid] = {"ok": False, "reason": f"unknown verb {verb!r}"}
            continue
        if verb == "move":
            dest = intent.get("target_location")
            if dest not in LOCATIONS:
                results[aid] = {"ok": False, "reason": "no such location"}
                continue
            if dest != loc and dest not in ADJACENCY.get(loc, set()):
                results[aid] = {"ok": False, "reason": f"cannot reach {dest} from {loc} in one tick"}
                continue
            me["location"] = dest
            events.append(new_event("move", [aid], dest, **{"from": loc, "to": dest}))
            results[aid] = {"ok": True}
        elif verb in ("speak", "ask", "confront"):
            target = intent.get("target")
            if target and world["agents"][target]["location"] != loc:
                events.append(new_event("speak_failed", [aid], loc, target=target,
                                        topic=intent.get("topic"), reason="target_left"))
                results[aid] = {"ok": False, "reason": "target_left"}
                continue
            interrupted = interactions.take_floor(loc, aid)
            ev = new_event(verb, [aid] + ([target] if target else []), loc,
                           topic=intent.get("topic"), claim=intent.get("claim"),
                           text=intent.get("text"), interrupted=interrupted,
                           session_location=loc)
            events.append(ev)
            results[aid] = {"ok": True, "event_id": ev["event_id"], "interrupted": interrupted}
        elif verb == "search":
            place = intent.get("target_location") or loc
            found = []
            if place == "back_room" and world["objects"]["anonymous_letter"]["location"] == "back_room":
                found.append("anonymous_letter")
                world["objects"]["anonymous_letter"]["seen_by"].append(aid)
            events.append(new_event("search", [aid], place, found=found))
            results[aid] = {"ok": True, "found": found}
        elif verb == "show":
            obj = intent.get("object")
            target = intent.get("target")
            if obj == "anonymous_letter" and aid == world["objects"]["anonymous_letter"]["holder"]:
                if target and world["agents"][target]["location"] == loc:
                    world["objects"]["anonymous_letter"]["seen_by"].append(target)
                    events.append(new_event("show", [aid, target], loc, object=obj))
                    results[aid] = {"ok": True}
                else:
                    results[aid] = {"ok": False, "reason": "target_not_here"}
            else:
                results[aid] = {"ok": False, "reason": "object_not_held"}
        elif verb == "refuse":
            events.append(new_event("refuse", [aid], loc, topic=intent.get("topic"),
                                    target=intent.get("target")))
            results[aid] = {"ok": True}
        elif verb == "listen":
            events.append(new_event("listen", [aid], loc, target=intent.get("target"),
                                    topic=intent.get("topic")))
            interactions.register_listener(world, aid)
            results[aid] = {"ok": True}
        elif verb in ("observe", "work", "wait", "give", "take"):
            events.append(new_event(verb, [aid], loc, target=intent.get("target"),
                                    topic=intent.get("topic")))
            results[aid] = {"ok": True}
    world["last_action_results"] = results
    return events


class WorldEngine:
    def __init__(self, configs: dict, seed: int = 42):
        self.rng = random.Random(seed)
        self.tick_no = 0
        self.event_seq = [0]
        self.interactions = InteractionManager()
        self.log: list[dict] = []
        agents = {}
        for aid, cfg in configs.items():
            agents[aid] = {"id": aid, "name": cfg.name, "role": cfg.role,
                           "location": cfg.start_location,
                           "initiative": 0.5 + cfg.personality["impulsiveness"]}
        self.state = {
            "tick": 0, "time": "21:00", "weather": "clear", "agents": agents,
            "locations": {
                "bar": {"objects": ["counter", "candle", "wine_shelf"]},
                "tables": {"objects": ["oak_table", "empty_glasses"]},
                "door": {"objects": ["door_bell"]},
                "back_room": {"objects": ["drawer", "ledger_shelf"]},
                "outside": {"objects": ["street_lamp"]},
            },
            "objects": {"anonymous_letter": {"holder": "owner", "location": "back_room",
                                             "seen_by": ["owner"]}},
            "last_action_results": {},
        }

    def snapshot(self) -> dict:
        return copy.deepcopy(self.state)

    def step(self, intents: dict[str, dict]) -> list[dict]:
        self.tick_no += 1
        self.state["tick"] = self.tick_no
        minutes = 21 * 60 + self.tick_no * 5
        self.state["time"] = f"{minutes // 60:02d}:{minutes % 60:02d}"
        events = resolve(self.state, intents, self.rng, self.event_seq,
                         self.interactions)
        for sev in self.interactions.sync(self.state, self.tick_no):
            self.event_seq[0] += 1
            sev.update({"event_id": f"E{self.event_seq[0]:04d}", "tick": self.tick_no})
            events.append(sev)
        for ev in events:
            if ev["type"] in ("speak", "ask", "confront") and ev.get("ok", True):
                self.interactions.record_utterance(self.state, ev["location"], ev,
                                                   bool(ev.get("interrupted")))
        if self.rng.random() < 0.35:
            kind, loc, text = self.rng.choice(AMBIENT)
            if kind == "rain_starts" and self.state["weather"] == "rain":
                kind, loc, text = "candle_flickers", "bar", AMBIENT[2][2]
            if kind == "rain_starts":
                self.state["weather"] = "rain"
            self.event_seq[0] += 1
            events.append({"event_id": f"E{self.event_seq[0]:04d}", "tick": self.tick_no,
                           "type": "ambient", "actors": [], "location": loc or "bar",
                           "topic": kind, "text": text})
        self.log.extend(events)
        return events
