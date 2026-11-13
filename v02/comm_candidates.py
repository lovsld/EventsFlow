"""Communication Candidate Generator (V0.2-C C2, design section 3).

A pure generator producing the SOCIAL candidate family — answers,
proactive assertions, denials, clarifications, disclosures,
withholds — which then compete in the rule provider's ONE scoring
market (design 3.1: no second channel, no private selector). It
replaces the B-era _social_candidates.

Its inputs are exclusively the speaker's OWN subjective materials
(design 3.2, the information-isolation gate): the pending-question
queue, the speaker's own goals' leaf propositions, its disclosure
duties, propositions it perceived others inquire about (topic
heat), and its own high-confidence beliefs. It never reads another
agent's goals, beliefs or trust, and never objective world state.

Hard rules implemented here (not priced — constructed):
  * Answer obligation (design 3.3): for every unexpired queue
    question whose proposition the speaker is ELIGIBLE to assert,
    an answer candidate MUST be generated. Selection is still the
    market's; withhold/deny candidates are generated alongside.
  * Withhold veto (design 3.5): an assert/answer candidate whose
    utterance event would light one of the speaker's own goals'
    failure_condition event_occurred leaves is NOT generated; it
    is reported in the returned vetoed list (census evidence).
  * Disclosure duties (design 3.5): while a duty's condition holds
    in the speaker's view, its target is present, and the speaker
    has not yet told that target, a disclose candidate MUST be
    generated every tick.
"""
from __future__ import annotations

from . import conditions
from .info_value import ASSERT_GENERATION_FLOOR, URGENCY_IV, ignorance

CLARIFY_BAND = (0.3, 0.7)


def _proposition_of(entry: dict, scenario) -> str | None:
    prop = entry.get("proposition")
    if prop:
        return prop
    cdef = scenario.claims.get(entry.get("claim_key") or "")
    return cdef.proposition if cdef else None


def effective_queue(state) -> list[dict]:
    """The pending-question queue, with the pre-C single slot as
    the migration/legacy fallback (design 3.3): a state whose queue
    is empty but whose legacy slot is set (an old checkpoint, a
    hand-built fixture) behaves as a one-element queue."""
    queue = list(getattr(state, "pending_questions", None) or [])
    if queue:
        return queue
    slot = getattr(state, "pending_question", None)
    if slot:
        return [{"asker": slot.get("from"),
                 "claim_key": slot.get("claim"),
                 "proposition": None,
                 "tick_asked": slot.get("tick", 0),
                 "addressed_to_me": slot.get("addressed_to_me", True)}]
    return []


def _would_light_failure(view, goals, *, claim: str, target: str,
                         tick: int) -> str | None:
    """The withhold hard check (design 3.5): synthesize the
    utterance event (speak, self, claim) and test it against every
    event_occurred leaf of my own goals' failure conditions with
    the existing matcher. Returns the goal key it would light."""
    ev = {"type": "speak", "actor": view.agent_id, "target": target,
          "claim": claim, "tick": tick}
    for g in goals:
        if not g.failure_condition:
            continue
        for leaf in conditions.all_leaves(g.failure_condition):
            if leaf.get("type") == "event_occurred" and \
                    conditions._event_matches(ev, leaf, view):
                return g.key
    return None


def _relevance_map(view, goals, scenario) -> dict[str, float]:
    """proposition -> relevance for proactive asserts (design
    3.4): 1.0 when the proposition is one of my own goals' leaf
    propositions, 0.5 for duty / topic-heat propositions."""
    rel: dict[str, float] = {}

    def bump(prop, value):
        if prop and value > rel.get(prop, 0.0):
            rel[prop] = value

    for g in goals:
        for cond in (g.success_condition, g.activation_condition):
            if not cond:
                continue
            for leaf in conditions.all_leaves(cond):
                t = leaf.get("type")
                if t == "belief_threshold":
                    bump(leaf["belief"], 1.0)
                elif t == "event_occurred" and leaf.get("claim"):
                    cdef = scenario.claims.get(leaf["claim"])
                    if cdef is not None:
                        bump(cdef.proposition, 1.0)
                elif t in ("object_possession", "object_found",
                           "object_at_location"):
                    decl = scenario.objects.get(leaf["object"])
                    if decl is not None:
                        bump(decl.fact_belief, 1.0)
    # topic heat: propositions I perceived others inquire about
    for ev in view.perceived_events:
        cdef = scenario.claims.get(ev.get("claim") or "")
        if cdef is not None and cdef.stance == "inquire":
            bump(cdef.proposition, 0.5)
    return rel


def _told_target(view, scenario, target, prop) -> bool:
    """Have I already DELIVERED this proposition to this target?
    Derived from my own action history — no new bookkeeping.

    V0.2-C fix round (transmission truthfulness, the same rule
    conditions.event_occurred now applies): a speech record
    counts only if the world confirmed it (`ok` is not False).
    An utterance that failed — the addressee had already left,
    the world logged speak_failed — told nobody anything and
    discharges no duty. Records without an ok field (legacy
    fixtures) keep the historical reading."""
    for a in view.own_actions:
        if a.get("ok") is False:
            continue
        if a.get("type") not in ("speak", "confront"):
            continue
        if a.get("target") != target:
            continue
        claim = a.get("claim")
        cdef = scenario.claims.get(claim or "") if claim else None
        if cdef is not None and cdef.proposition == prop:
            return True
    return False


def applicable_duties(view, config, scenario, colocated) -> list[dict]:
    """Disclosure duties currently in force for this speaker
    (design 3.5): the duty's `when` holds in my view, a declared
    target is present, and I have not yet told that target the
    proposition (derived from my own action history — no new
    bookkeeping). Returns [{duty, target, claim}] with the assert
    claim I am eligible to voice, if any."""
    out = []
    duties = getattr(config, "disclosure_duties", None) or []
    if not duties:
        return out
    for duty in duties:
        when = duty.get("when")
        if when is not None and not conditions.is_satisfied(when, view):
            continue
        prop = duty.get("proposition")
        targets = duty.get("targets")
        if targets == "colocated":
            present = list(colocated)
        else:
            present = [t for t in (targets or []) if t in colocated]
        for target in present:
            if not _told_target(view, scenario, target, prop):
                out.append({"duty": duty, "target": target})
    return out


def undelivered_duties(view, config, scenario, colocated) -> list[dict]:
    """Disclosure duties in force whose NAMED target is absent and
    has not yet been truly told (V0.2-C fix round — the return-leg
    driver for duty-driven seeking). Same `when` and told
    semantics as applicable_duties: the two functions partition a
    duty's named targets by presence. Duties with
    targets="colocated" name no recipient to seek and never
    appear here. Reads only my own view, my own duties data and
    my own action history."""
    out = []
    duties = getattr(config, "disclosure_duties", None) or []
    if not duties:
        return out
    present = set(colocated)
    for duty in duties:
        when = duty.get("when")
        if when is not None and not conditions.is_satisfied(when, view):
            continue
        targets = duty.get("targets")
        if not targets or targets == "colocated":
            continue
        prop = duty.get("proposition")
        for target in targets:
            if target in present:
                continue
            if not _told_target(view, scenario, target, prop):
                out.append({"duty": duty, "target": target})
    return out


def proposition_held_since(view, prop) -> int | None:
    """The tick my belief in `prop` was most recently evidenced
    (max Evidence.observed_at) — how long I have held this
    knowledge, from my own evidence records only. None when I
    hold no such belief or it carries no evidence."""
    b = view.beliefs.get(prop)
    if b is None or not b.evidence:
        return None
    return max(e.observed_at for e in b.evidence)


def generate_comm_candidates(*, view, scenario, config, goals,
                             pending_questions, colocated, counts,
                             tick, line_fn,
                             assert_claim_for) -> dict:
    """Generate the communication candidate family.

    view / scenario / config: the speaker's subjective view, the
    run's Scenario Definition, the speaker's CharacterConfig.
    goals: the speaker's candidate pool goals (active + pursuit).
    pending_questions: the effective queue (see effective_queue).
    line_fn(claim, counts) -> utterance text; eligible_assert /
    assert_claim_for: the provider's claim-eligibility helpers
    (single implementation of the ClaimDef rules).

    Returns {"candidates": [...], "vetoed": [{sig, goal, reason}]}.
    Candidate dicts carry the provider's usual fields plus
    comm_kind / obligation / obligation_waited / info_override.
    """
    cands: list[dict] = []
    vetoed: list[dict] = []
    me = view.agent_id

    def speech(verb, target, claim, *, comm_kind, goal_key=None,
               prop=None, obligation=False, waited=0,
               info_override=None, known_prop=None, source="social"):
        action = {"type": verb, "target": target, "topic": claim,
                  "claim": claim, "content": line_fn(claim, counts)}
        cand = {"action": action, "source": source,
                "goal_key": goal_key, "leaf": None, "prop": prop,
                "obj": None, "comm_kind": comm_kind,
                "obligation": obligation,
                "obligation_waited": waited,
                "info_override": info_override}
        if known_prop is not None:
            cand["known_prop"] = known_prop
        # withhold hard veto (design 3.5): self-protection is a
        # veto, never a price — a goal's failure semantics may not
        # be diluted by scoring
        if verb in ("speak", "confront") and comm_kind in (
                "answer", "assert", "disclose"):
            lit = _would_light_failure(view, goals, claim=claim,
                                       target=target, tick=tick)
            if lit is not None:
                vetoed.append({
                    "sig": f"{verb}:{target}:{claim}",
                    "goal": lit, "comm_kind": comm_kind,
                    "reason": "assert would light own goal "
                              f"{lit!r} failure_condition"})
                return
        cands.append(cand)

    asked_props: set[str] = set()
    # ---------- the answer obligation (design 3.3) ----------
    for entry in pending_questions:
        if not entry.get("addressed_to_me", True):
            continue
        asker = entry.get("asker")
        if asker not in colocated:
            continue
        prop = _proposition_of(entry, scenario)
        waited = max(0, tick - int(entry.get("tick_asked", tick)))
        if prop:
            asked_props.add(prop)
            ans = assert_claim_for(prop)
            conf = view.belief_conf(prop)
            if ans and entry.get("via", "ask") == "ask":
                # HARD GUARANTEE: the answer candidate exists.
                # Its information value is recipient-side and
                # priced by the design's flat rule (3.3(1)): the
                # asker asked me by name, so the telling is worth
                # 1.0 to them — regardless of how certain *I* am.
                speech("speak", asker, ans, comm_kind="answer",
                       prop=prop, obligation=True, waited=waited,
                       info_override=1.0)
            if ans is None or CLARIFY_BAND[0] <= conf <= CLARIFY_BAND[1]:
                # I cannot answer (not eligible), or my knowledge
                # is in the ambiguous band: counter-ask to clarify
                inquire = scenario.claim_for_proposition(prop, "inquire")
                if inquire:
                    speech("ask", asker, inquire, comm_kind="clarify",
                           prop=prop, obligation=True, waited=waited)
        deny = scenario.deny_claim()
        if deny:
            speech("speak", asker, deny, comm_kind="deny",
                   obligation=True, waited=waited, known_prop=prop)
        cands.append({"action": {"type": "refuse", "target": asker,
                                 "topic": entry.get("claim_key")},
                      "source": "social", "goal_key": None,
                      "leaf": None, "prop": None, "obj": None,
                      "comm_kind": "withhold", "obligation": False,
                      "obligation_waited": waited,
                      "info_override": None})
        cands.append({"action": {"type": "observe"},
                      "source": "social", "goal_key": None,
                      "leaf": None, "prop": None, "obj": None,
                      "comm_kind": None, "obligation": False,
                      "obligation_waited": 0, "info_override": None})

    # ---------- proactive asserts (design 3.4) ----------
    relevance = _relevance_map(view, goals, scenario)
    for duty in (getattr(config, "disclosure_duties", None) or []):
        if duty.get("proposition"):
            relevance.setdefault(duty["proposition"], 0.5)
            if relevance[duty["proposition"]] < 0.5:
                relevance[duty["proposition"]] = 0.5
    for prop in sorted(relevance):
        if prop in asked_props:
            continue        # the obligation path owns this one
        claim = assert_claim_for(prop)
        if not claim:
            continue
        conf = view.belief_conf(prop)
        rel = relevance[prop]
        for target in colocated:
            if target == me:
                continue
            ign = ignorance(view, target, prop, scenario)
            if ign <= 0.0:
                continue    # they already know — do not re-tell
            value = ign * rel * conf
            if value >= ASSERT_GENERATION_FLOOR:
                speech("speak", target, claim, comm_kind="assert",
                       prop=prop, info_override=ign * rel)

    # ---------- disclosure duties (design 3.5) ----------
    for item in applicable_duties(view, config, scenario, colocated):
        duty, target = item["duty"], item["target"]
        prop = duty.get("proposition")
        claim = assert_claim_for(prop) if prop else None
        if not claim:
            continue        # not eligible to voice it: no candidate
        speech("speak", target, claim, comm_kind="disclose",
               prop=prop,
               info_override=URGENCY_IV.get(duty.get("urgency"),
                                            0.7))

    # ---------- greeting fallback (pre-C behaviour, kept) ----------
    if not counts and not any(
            e.get("addressed_to_me", True) and e.get("asker") in colocated
            for e in pending_questions):
        social = scenario.social_claim()
        if social:
            for other in colocated:
                speech("speak", other, social, comm_kind=None,
                       source="greeting")

    return {"candidates": cands, "vetoed": vetoed}
