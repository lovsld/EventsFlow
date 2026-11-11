"""World configuration — the single source for the world's static
structure (V0.2-A Phase 5, experiment 3a).

Before this module, the location graph existed as FOUR separate
ADJACENCY literals (world.py, broker.py, context_builder.py and the
copies importers picked up), and the location scenery / key-object
initial state were hardcoded inside WorldEngine. A scenario is now
data: locations, scenery, adjacency, key objects (holder / location /
seen_by) and ambient events live here (or in any dict of the same
shape handed to WorldEngine / run_simulation), and the engine +
resolver read them generically — search finds any object whose
objective location is the searched place, show works for any held
object. The tavern default below reproduces the historical world
exactly (same order, same contents), so the canonical trajectory is
bit-identical before and after the refactor.
"""
from __future__ import annotations

import copy

DEFAULT_WORLD: dict = {
    "locations": ["bar", "tables", "door", "back_room", "outside"],
    "scenery": {
        "bar": ["counter", "candle", "wine_shelf"],
        "tables": ["oak_table", "empty_glasses"],
        "door": ["door_bell"],
        "back_room": ["drawer", "ledger_shelf"],
        "outside": ["street_lamp"],
    },
    "adjacency": {
        "bar": ["tables"],
        "tables": ["bar", "door", "back_room"],
        "door": ["tables", "outside"],
        "back_room": ["tables"],
        "outside": ["door"],
    },
    "objects": {
        "anonymous_letter": {"holder": "owner", "location": "back_room",
                             "seen_by": ["owner"]},
    },
    "ambient": [
        ("rain_starts", None, "窗外下起雨来，酒馆里的人今晚大概走不了太早。"),
        ("bell_rings", "door", "酒馆门口的铃铛响了一声，又没有人进来。"),
        ("candle_flickers", "bar", "吧台的蜡烛晃了晃，老板的目光跟着晃了一下。"),
        ("distant_shout", "outside", "街上远处有人喊了一嗓子，很快没了声音。"),
        ("cat_knocks_glass", "tables", "一只野猫碰倒了桌边的空杯子，碎在地上。"),
    ],
}


def adjacency_of(world_config: dict | None = None) -> dict[str, set]:
    """The location graph as {location: {neighbours}} — the ONE
    definition every consumer (engine, broker, context builder,
    prediction, audit) shares."""
    cfg = world_config or DEFAULT_WORLD
    return {loc: set(neighbours)
            for loc, neighbours in cfg["adjacency"].items()}


def locations_of(world_config: dict | None = None) -> list[str]:
    cfg = world_config or DEFAULT_WORLD
    return list(cfg["locations"])


def initial_locations_state(world_config: dict | None = None) -> dict:
    cfg = world_config or DEFAULT_WORLD
    return {loc: {"objects": list(cfg["scenery"].get(loc, []))}
            for loc in cfg["locations"]}


def initial_objects_state(world_config: dict | None = None) -> dict:
    cfg = world_config or DEFAULT_WORLD
    return copy.deepcopy(cfg["objects"])


def ambient_of(world_config: dict | None = None) -> list[tuple]:
    cfg = world_config or DEFAULT_WORLD
    return [tuple(entry) for entry in cfg["ambient"]]
