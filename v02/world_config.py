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
    # V0.2-B: the default world IS the tavern Scenario Definition
    # (design section 3.1) — the structural keys below keep their
    # historical shape/contents; the objects gain capability
    # attributes and the claims/speech_lines sections carry what used
    # to be hardcoded in rule_provider.TOPIC_LINES and agent._interpret.
    "scenario_id": "tavern_letter",
    "version": 1,
    "locations": ["bar", "tables", "door", "back_room", "outside"],
    "scenery": {
        "bar": ["counter", "candle", "wine_shelf"],
        "tables": ["oak_table", "empty_glasses"],
        "door": ["door_bell"],
        # the drawer is why the back room affords searching — a data
        # fact about the scenery, not the location's name (design 3.2)
        "back_room": [{"id": "drawer", "searchable": True}, "ledger_shelf"],
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
        "anonymous_letter": {
            "holder": "owner", "location": "back_room",
            "seen_by": ["owner"],
            "searchable": True, "movable": True, "displayable": True,
            "on_found": "inspect",
            "fact_belief": "anonymous_letter_exists",
            "reveals": [{"belief": "mayor_embezzled", "reliability": 0.6}],
        },
    },
    # Claim classes (design 3.5): proposition = what hearers gain
    # testimony evidence for; speaker_belief = the experience that
    # grounds a first-person report (its hearer-side conclusion is
    # the proposition). mayor_hint / mayor_late_payment / answer_mayor
    # are one proposition at three declared reliabilities, exactly as
    # the design migration table prescribes.
    "claims": {
        "greeting": {"proposition": None, "stance": "assert",
                     "basis_required": False},
        "smalltalk": {"proposition": None, "stance": "assert",
                      "basis_required": False},
        "business_proposal": {"proposition": "business_opportunity",
                              "stance": "assert", "base_reliability": 0.5,
                              "basis_required": False},
        "mayor_hint": {"proposition": "mayor_embezzled", "stance": "assert",
                       "base_reliability": 0.45,
                       "speaker_min_confidence": 0.45},
        "mayor_late_payment": {"proposition": "mayor_embezzled",
                               "stance": "assert", "base_reliability": 0.5,
                               "speaker_belief": "mayor_business_deal"},
        "answer_mayor": {"proposition": "mayor_embezzled", "stance": "assert",
                         "base_reliability": 0.5,
                         "speaker_belief": "mayor_business_deal"},
        "investigation_probe": {"proposition": "mayor_embezzled",
                                "stance": "inquire", "sensitive": True},
        "letter_question": {"proposition": "anonymous_letter_exists",
                            "stance": "inquire", "sensitive": True,
                            "speaker_min_confidence": 0.1},
        "letter_discussion": {"proposition": "anonymous_letter_exists",
                              "stance": "assert", "base_reliability": 1.0,
                              "speaker_min_confidence": 0.9,
                              "speaker_kind": "experienced"},
        "letter_origin_inquiry": {"proposition": "letter_source_identified",
                                  "stance": "inquire", "sensitive": True},
        "letter_origin_report": {"proposition": "letter_source_identified",
                                 "stance": "assert", "base_reliability": 0.85,
                                 "speaker_belief": "anonymous_letter_exists",
                                 "speaker_min_confidence": 0.9,
                                 "speaker_kind": "experienced"},
        "bluff_hint": {"proposition": "speaker_knows_secret",
                       "stance": "assert", "base_reliability": 0.45,
                       "basis_required": False},
        "denial": {"stance": "deny"},
    },
    # Pure content: how a claim sounds, never whether to say it
    # (verbatim from the old rule provider's TOPIC_LINES).
    "speech_lines": {
        "greeting": "{name}：晚上好，雨夜里酒馆可是个好去处。",
        "business_proposal": [
            "{name}：老板，我手里有批货，镇上要是有销路，咱们都有赚头。",
            "{name}：那批货的事，老板真不考虑？价格好商量。",
            "{name}：我也不绕了——货压在手里，镇上只有你这儿能走量。"],
        "mayor_late_payment": "{name}：说句实在话，镇长那笔粮食生意，钱拖了我两个月才结清。",
        "mayor_hint": "{name}：（压低声音）镇上那笔公共资金……数目好像对不上。",
        "investigation_probe": [
            "{name}：老板，我例行问一句——这几天酒馆有没有什么异常的人或事？",
            "{name}：再问一次，这几天后屋那边，有人动过什么吗？",
            "{name}：老板，你越是不说，我越得查清楚。"],
        "letter_question": "{name}：我听说……有一封信？一封关于镇长的信，是不是在你这儿？",
        "letter_discussion": "{name}：那封信，我已经看过了。信里说的事，你打算怎么办？",
        "letter_origin_inquiry": "{name}：我把话问明白——这封信，到底是谁写、从哪儿来的？",
        "letter_origin_report": "{name}：信是在后屋抽屉里放着的，纸和字迹都不像镇上熟人的。",
        "denial": ["{name}：我这小酒馆能有什么异常？各位喝酒就是了，别的不归我管。",
                   "{name}：问几遍都一样，我什么都不知道。"],
        "bluff_hint": "{name}：（笑）这镇上的秘密啊，我知道的可能比你们想的多一点。",
        "answer_mayor": "{name}：镇长的钱袋子紧不紧，我一个做小买卖的（看局势）……只能说，不太好看。",
        "smalltalk": "{name}：这雨下得，路都不好走了。",
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
    # V0.2-B: scenery entries may be {"id":..., "searchable":...}
    # objects in the Scenario Definition; the world state (and hence
    # perceptions' visible_objects) carries plain ids, as before.
    return {loc: {"objects": [e["id"] if isinstance(e, dict) else e
                              for e in cfg["scenery"].get(loc, [])]}
            for loc in cfg["locations"]}


def initial_objects_state(world_config: dict | None = None) -> dict:
    cfg = world_config or DEFAULT_WORLD
    # Objective STATE only (holder / location / seen_by): capability
    # attributes are scenario data the Resolver reads from the config,
    # not world state (V0.2-B design 3.1).
    return {oid: {"holder": o.get("holder"), "location": o.get("location"),
                  "seen_by": list(o.get("seen_by", []))}
            for oid, o in cfg["objects"].items()}


def ambient_of(world_config: dict | None = None) -> list[tuple]:
    cfg = world_config or DEFAULT_WORLD
    return [tuple(entry) for entry in cfg["ambient"]]
