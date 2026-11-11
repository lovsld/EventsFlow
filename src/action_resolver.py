"""Action Resolver.

Takes the intents submitted independently by all agents for one tick and
decides what objectively happens:

- Initiative order: each agent has a fixed 'initiative' derived from its
  impulsiveness (world-side copy of the public trait) + per-tick jitter from
  the WORLD's rng (agents do not control it). Higher initiative acts first.
- A 'speak/ask/confront' intent fails if the target already moved away this
  tick (target no longer co-located at resolution time).
- If two agents speak in the same session in one tick, the later one is
  flagged as an INTERRUPTION — this is how interruptions emerge instead of
  being scripted.
- A 'search' of the back_room drawer finds the anonymous letter ONLY if the
  letter is physically there (objective object state), regardless of who
  searches. Finding != understanding: the event records the discovery, the
  discoverer's belief update is its own business.
- Illegal intents (unknown verb, moving to a non-adjacent location in one
  tick) are rejected and logged with a reason; the agent learns of the
  rejection next tick through its perception ('action_result').
"""
from __future__ import annotations

from .perception import ADJACENCY, LOCATIONS

VERBS = {"move", "speak", "ask", "confront", "observe", "listen", "work",
         "search", "refuse", "wait", "give", "show", "take"}


def resolve(world: dict, intents: dict[str, dict], rng, event_seq: list[int]) -> list[dict]:
    def new_event(type_, actors, location, **kw):
        event_seq[0] += 1
        ev = {"event_id": f"E{event_seq[0]:04d}", "tick": world["tick"], "type": type_,
              "actors": actors, "location": location}
        ev.update(kw)
        return ev

    events: list[dict] = []
    order = sorted(
        intents.items(),
        key=lambda kv: -(world["agents"][kv[0]]["initiative"] + rng.random() * 0.2),
    )
    spoken_in_session: set[str] = set()
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
            if dest != loc and dest not in ADJACENCY.get(loc, set()) and loc != dest:
                # one tick = one room step (door is the hub); long jumps rejected
                if not (loc in ADJACENCY and dest in ADJACENCY.get(loc, set())):
                    results[aid] = {"ok": False, "reason": f"cannot reach {dest} from {loc} in one tick"}
                    continue
            me["location"] = dest
            events.append(new_event("move", [aid], dest, **{"from": loc, "to": dest}))
            results[aid] = {"ok": True}

        elif verb in ("speak", "ask", "confront"):
            target = intent.get("target")
            if target and world["agents"][target]["location"] != loc:
                events.append(new_event("speak_failed", [aid], loc,
                                        target=target, topic=intent.get("topic"),
                                        reason="target_left"))
                results[aid] = {"ok": False, "reason": "target_left"}
                continue
            interrupted = loc in spoken_in_session
            spoken_in_session.add(loc)
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

        elif verb in ("observe", "listen", "work", "wait", "give", "take"):
            events.append(new_event(verb, [aid], loc,
                                    target=intent.get("target"), topic=intent.get("topic")))
            results[aid] = {"ok": True}
        else:  # pragma: no cover
            results[aid] = {"ok": False, "reason": "unhandled"}

    world["last_action_results"] = results
    return events
