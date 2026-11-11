"""World Engine — the single authority over objective reality.

Owns: time, locations, agent positions (public), physical objects,
the objective event log, and environment events. Commits one tick at a time
after the Action Resolver runs. It NEVER reads agent beliefs/memories and
never decides what an agent does.

Environment events are drawn from the world's own rng: they are weather /
ambient happenings, not plot instructions. No ending is scripted anywhere.
"""
from __future__ import annotations

import random

from .action_resolver import resolve
from .interaction import InteractionManager

AMBIENT = [
    ("rain_starts", None, "窗外下起雨来，酒馆里的人今晚大概走不了太早。"),
    ("bell_rings", "door", "酒馆门口的铃铛响了一声，又没有人进来。"),
    ("candle_flickers", "bar", "吧台的蜡烛晃了晃，老板的目光跟着晃了一下。"),
    ("distant_shout", "outside", "街上远处有人喊了一嗓子，很快没了声音。"),
    ("cat_knocks_glass", "tables", "一只野猫碰倒了桌边的空杯子，碎在地上。"),
]


class WorldEngine:
    def __init__(self, profiles: dict[str, dict], seed: int = 42):
        self.rng = random.Random(seed)
        self.tick_no = 0
        self.event_seq = [0]
        self.interactions = InteractionManager()
        self.log: list[dict] = []
        agents = {}
        for aid, p in profiles.items():
            agents[aid] = {
                "id": aid, "name": p["name"], "role": p["role"],
                "location": p["start_location"],
                "initiative": 0.5 + p["personality"]["impulsiveness"],
            }
        self.state = {
            "tick": 0, "time": "21:00", "weather": "clear",
            "agents": agents,
            "locations": {
                "bar": {"objects": ["counter", "candle", "wine_shelf"]},
                "tables": {"objects": ["oak_table", "empty_glasses"]},
                "door": {"objects": ["door_bell"]},
                "back_room": {"objects": ["drawer", "ledger_shelf"]},
                "outside": {"objects": ["street_lamp"]},
            },
            "objects": {
                "anonymous_letter": {"holder": "A", "location": "back_room", "seen_by": ["A"]},
            },
            "last_action_results": {},
        }

    def snapshot(self) -> dict:
        import copy
        return copy.deepcopy(self.state)

    def step(self, intents: dict[str, dict]) -> list[dict]:
        """Commit one tick. intents: {agent_id: intent dict}. Returns committed events."""
        self.tick_no += 1
        self.state["tick"] = self.tick_no
        minutes = 21 * 60 + self.tick_no * 5
        self.state["time"] = f"{minutes // 60:02d}:{minutes % 60:02d}"

        events = resolve(self.state, intents, self.rng, self.event_seq)

        # session lifecycle after movement/speech resolution
        for sev in self.interactions.sync(self.state, self.tick_no):
            self.event_seq[0] += 1
            sev.update({"event_id": f"E{self.event_seq[0]:04d}", "tick": self.tick_no})
            events.append(sev)
        for ev in events:
            if ev["type"] in ("speak", "ask", "confront") and ev.get("ok", True):
                self.interactions.record_utterance(self.state, ev["location"], ev,
                                                   bool(ev.get("interrupted")))

        # ambient environment event, 35% chance, world rng only
        if self.rng.random() < 0.35:
            kind, loc, text = self.rng.choice(AMBIENT)
            if kind == "rain_starts" and self.state["weather"] == "rain":
                kind, loc, text = "candle_flickers", "bar", AMBIENT[2][2]  # 雨不会"开始"两次
            if kind == "rain_starts":
                self.state["weather"] = "rain"
            self.event_seq[0] += 1
            events.append({"event_id": f"E{self.event_seq[0]:04d}", "tick": self.tick_no,
                           "type": "ambient", "actors": [], "location": loc or "bar",
                           "topic": kind, "text": text})

        self.log.extend(events)
        return events
