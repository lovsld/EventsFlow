"""Information Value (V0.2-C, design sections 3.4 / 4.1).

ONE definition, shared by the communication generator (C2) and the
rule provider's scoring (unified in C3):

    IV(a) = sum over the action's targets x of
            uncertainty(x) x relevance(x)

Per-action instantiations (design 4.1): ask(P) keeps B's
speaker-side form 1 - conf(P) (the value of asking is in MY
uncertainty); assert/answer/disclose(P -> T) are recipient-side
(answer: the flat asked-by-name 1.0 of design 3.3(1); assert:
ignorance x relevance; disclose: the duty's urgency value);
search(L) = 1 - searched_certainty(L); move = 0.8 x the search
it serves; observe/listen use the documented SENSOR_IV constant.
The branch implementations live here or carry an info_override
set by their generator, so no second copy of any branch exists.

The recipient-side uncertainty for speech is the speaker's
PERCEPTION-HISTORY estimate of the hearer's ignorance — built only
from what the speaker itself perceived (design 3.4):

  - I perceived T INQUIRE about P   -> ignorance 1.0
    (they publicly do not know)
  - I perceived T ASSERT P          -> ignorance 0.0
    (they already know; do not generate a repeat telling)
  - no record                       -> 0.5 (neutral prior)

The approximation is knowingly imperfect: T may have learned P
elsewhere an hour ago, or may be feigning ignorance. That is the
subjective error an agent is supposed to make — it acts on its own
perception history, and the world corrects it (a redundant assert
is just one more testimony event, digested by evidence dedup).
"""
from __future__ import annotations

# Disclosure-duty urgency -> information value (design 3.3): a duty
# to inform is priced by how much the telling matters, declared as
# data, mapped here once.
URGENCY_IV = {"high": 1.0, "normal": 0.7, "low": 0.4}

# A proactive assert is generated only at/above this value
# (ignorance x relevance x my confidence) — a generic threshold,
# calibration-adjustable (design 3.4).
ASSERT_GENERATION_FLOOR = 0.25

# V0.2-C C3 (design 4.1): how certain I am about a place I have
# already searched. Not 1.0 — objects can be moved by others
# after my search; the residual 0.1 of uncertainty is the world
# possibility that things changed. (B priced this as a flat 0.15
# information discount for searched places; the unified IV
# re-expresses the same idea in probability language.)
SEARCHED_CERTAINTY = 0.9

# observe/listen price their information at this flat value
# (design 4.1): an environment sensor constant, documented as
# such — not an IV special case pretending to be a derivation.
SENSOR_IV = 0.3


def searched_certainty(view, place: str) -> float:
    """My certainty about what a search of `place` would find,
    from the derived searched-places set (design 4.1/4.3)."""
    return SEARCHED_CERTAINTY if place in view.searched_places else 0.0


def ignorance(view, target: str, proposition: str, scenario) -> float:
    """My estimate that `target` does not know `proposition`, from
    my own perceived speech history only. The LATEST relevant act
    wins: someone who asked and was later heard asserting the
    proposition counts as knowing it now."""
    estimate = None
    for ev in view.perceived_events:
        actors = ev.get("actors") or []
        actor = actors[0] if actors else ev.get("actor")
        if actor != target:
            continue
        claim = ev.get("claim")
        if not claim:
            continue
        cdef = scenario.claims.get(claim)
        if cdef is None or cdef.proposition != proposition:
            continue
        if cdef.stance == "inquire" or ev.get("type") == "ask":
            estimate = 1.0
        elif cdef.stance == "assert" and ev.get("type") in (
                "speak", "confront"):
            estimate = 0.0
    return 0.5 if estimate is None else estimate
