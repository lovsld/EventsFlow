"""Closed-vocabulary condition evaluator (V0.2-B, design section 3.3).

ONE pure evaluator shared by the GoalEvaluator, the PlanStepEvaluator
and the rule path's Candidate Generator, so a condition means exactly
the same thing in all three places — "completion" has a single
definition, and it lives here, not in the execution layer.

Vocabulary (CLOSED — a new predicate type is a code change + design
change + tests, never scenario data inventing types):

  object_possession  {object, holder}        0/1
  object_at_location {object, location}      0/1
  object_found       {object}                0/1
  belief_threshold   {belief, min_confidence, min_source_tier?}
  agent_at_location  {agent, location}       0/1
  event_occurred     {event_type?, actor?, target?, claim?, topic?,
                      object?, location?, within_ticks?, count?}
  trust_threshold    {other, min_trust}      ("any" = best relationship)
  goal_status        {goal, status}          0/1

Combinators: {"all": [...]}, {"any": [...]}, {"not": cond}.
Satisfaction is in [0, 1]; a condition is SATISFIED iff its
satisfaction >= 1.0. Boolean leaves are 0/1; belief/trust thresholds
are continuous (clamp01(value / threshold)); a belief leaf whose
min_source_tier is unmet is scaled by 0.5 and can therefore never
count as satisfied on testimony alone.

DOMAINS (design D6): the decision path evaluates ONLY subjective
conditions — predicates over the evaluating agent's own beliefs,
holdings, position, relationships and perception/action history,
captured in a SubjectiveView. Objective evaluation (reading a world
snapshot) exists solely for experiment-layer assertions and is a
separate function; a condition dict carrying "domain": "objective"
is REJECTED here, loudly, so objective facts can never leak into a
behaviour-driving condition by a data typo.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .protocol import SOURCE_TIER

TIER_ALIASES = {"direct": 3, "experienced": 3, "testimony": 2,
                "heard": 2, "inference": 1, "assumption": 0}


def tier_of(value) -> int:
    """A min_source_tier as declared in data: an int, a source-type
    name ("direct_observation"), or a coarse alias ("direct")."""
    if isinstance(value, int):
        return value
    if value in SOURCE_TIER:
        return SOURCE_TIER[value]
    return TIER_ALIASES[value]


@dataclass
class SubjectiveView:
    """Everything a subjective condition may read — all of it the
    evaluating agent's own state. Built by the agent runtime from its
    private CharacterState; evaluators receive ONLY this."""
    agent_id: str
    tick: int
    location: str
    beliefs: dict = field(default_factory=dict)      # key -> Belief
    trust: dict = field(default_factory=dict)        # other -> float
    holdings: set = field(default_factory=set)
    found_objects: set = field(default_factory=set)
    searched_empty_places: set = field(default_factory=set)
    object_sightings: dict = field(default_factory=dict)  # object -> location
    perceived_events: list = field(default_factory=list)  # structured dicts
    own_actions: list = field(default_factory=list)       # structured dicts
    goals: dict = field(default_factory=dict)        # key -> status

    def belief_conf(self, key: str) -> float:
        b = self.beliefs.get(key)
        return b.confidence if b else 0.0

    def belief_top_tier(self, key: str) -> int:
        b = self.beliefs.get(key)
        if b is None:
            return -1
        if b.evidence:
            return max(SOURCE_TIER[e.source_type] for e in b.evidence)
        from .protocol import KIND_TO_SOURCE
        return SOURCE_TIER[KIND_TO_SOURCE[b.kind]]


def build_view(agent, tick: int) -> SubjectiveView:
    """The subjective view of one agent, from its private state only."""
    st = agent.state
    return SubjectiveView(
        agent_id=st.character_id, tick=tick, location=st.location,
        beliefs={b.key: b for b in st.beliefs},
        trust={o: rel.get("trust", 0.3)
               for o, rel in st.relationships.items()},
        holdings=set(st.holdings), found_objects=set(st.found_objects),
        searched_empty_places=set(st.searched_empty_places),
        object_sightings=dict(st.object_sightings),
        perceived_events=list(st.perceived_events),
        own_actions=list(st.own_actions),
        goals={g.key: g.status for g in st.goals})


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def last_seen_location(view: SubjectiveView, agent_id: str) -> str | None:
    """Where the evaluating agent last perceived another agent to be:
    the destination of its latest perceived move, else the location
    of the latest perceived event it took part in."""
    loc = None
    for ev in view.perceived_events:
        if ev.get("actor") == agent_id:
            if ev.get("type") == "move" and ev.get("to"):
                loc = ev["to"]
            elif ev.get("location"):
                loc = ev["location"]
    return loc


def _event_matches(ev: dict, spec: dict, view: SubjectiveView) -> bool:
    etype = spec.get("event_type", spec.get("type_"))
    if etype is not None and ev.get("type") != etype:
        return False
    actor = spec.get("actor")
    if actor == "self":
        if ev.get("actor") != view.agent_id:
            return False
    elif actor == "any_other":
        if ev.get("actor") in (None, view.agent_id):
            return False
    elif actor is not None and ev.get("actor") != actor:
        return False
    target = spec.get("target")
    if target == "self":
        if ev.get("target") != view.agent_id:
            return False
    elif target is not None and ev.get("target") != target:
        return False
    for f in ("claim", "topic", "object", "location"):
        if spec.get(f) is not None and ev.get(f) != spec.get(f):
            return False
    within = spec.get("within_ticks")
    if within is not None and view.tick - int(ev.get("tick", 0)) >= within:
        return False
    return True


def _eval_leaf(cond: dict, view: SubjectiveView) -> tuple[float, dict]:
    t = cond.get("type")
    if t == "belief_threshold":
        key = cond["belief"]
        theta = float(cond.get("min_confidence", 1.0))
        conf = view.belief_conf(key)
        sat = _clamp01(conf / theta) if theta > 0 else (1.0 if conf > 0 else 0.0)
        detail = {"belief": key, "confidence": conf, "threshold": theta}
        if cond.get("min_source_tier") is not None:
            need = tier_of(cond["min_source_tier"])
            have = view.belief_top_tier(key)
            detail.update({"tier_required": need, "tier_present": have})
            if have < need:
                sat *= 0.5
                detail["tier_unmet"] = True
        return sat, detail
    if t == "trust_threshold":
        other, theta = cond["other"], float(cond.get("min_trust", 1.0))
        if other == "any":
            trust = max(view.trust.values(), default=0.0)
        elif other == "self":
            trust = 0.0
        else:
            trust = view.trust.get(other, 0.3)
        sat = _clamp01(trust / theta) if theta > 0 else 1.0
        return sat, {"other": other, "trust": trust, "threshold": theta}
    if t == "object_possession":
        obj, holder = cond["object"], cond.get("holder", "self")
        if holder in ("self", view.agent_id):
            return (1.0 if obj in view.holdings else 0.0), \
                {"object": obj, "holder": holder, "held": obj in view.holdings}
        conf = view.belief_conf(f"holds:{holder}:{obj}")
        return (1.0 if conf >= 0.9 else 0.0), \
            {"object": obj, "holder": holder, "holds_belief": conf}
    if t == "object_at_location":
        obj, loc = cond["object"], cond["location"]
        seen = view.object_sightings.get(obj)
        held_here = obj in view.holdings and view.location == loc
        return (1.0 if seen == loc or held_here else 0.0), \
            {"object": obj, "location": loc, "sighted_at": seen,
             "held_here": held_here}
    if t == "object_found":
        obj = cond["object"]
        return (1.0 if obj in view.found_objects else 0.0), {"object": obj}
    if t == "agent_at_location":
        who, loc = cond.get("agent", "self"), cond["location"]
        if who in ("self", view.agent_id):
            return (1.0 if view.location == loc else 0.0), \
                {"agent": who, "location": loc, "at": view.location}
        seen = last_seen_location(view, who)
        return (1.0 if seen == loc else 0.0), \
            {"agent": who, "location": loc, "last_seen_at": seen}
    if t == "event_occurred":
        history = list(view.perceived_events) + [
            {**a, "actor": view.agent_id} for a in view.own_actions]
        hits = [ev for ev in history if _event_matches(ev, cond, view)]
        need = int(cond.get("count", 1))
        return (1.0 if len(hits) >= need else 0.0), \
            {"matches": len(hits), "count_required": need}
    if t == "goal_status":
        status = view.goals.get(cond["goal"])
        return (1.0 if status == cond.get("status") else 0.0), \
            {"goal": cond["goal"], "status": status}
    raise ValueError(f"unknown predicate type {t!r} — the vocabulary "
                     "is closed (design section 3.3)")


def evaluate(condition: dict, view: SubjectiveView,
             path: str = "") -> tuple[float, list[dict]]:
    """(satisfaction, leaf_report) of a subjective condition.

    The leaf report names every leaf's satisfaction and the facts it
    read, so an audit can see WHICH sub-condition failed — completion
    decisions are explainable from the report alone."""
    if not isinstance(condition, dict):
        raise ValueError(f"condition must be a dict, got {condition!r}")
    if condition.get("domain") == "objective":
        raise ValueError(
            "objective-domain conditions are experiment-layer only and "
            "must never reach a decision-path evaluator (design D6)")
    if "all" in condition:
        sats, report = [], []
        for i, sub in enumerate(condition["all"]):
            s, r = evaluate(sub, view, f"{path}all[{i}].")
            sats.append(s)
            report.extend(r)
        return (min(sats) if sats else 1.0), report
    if "any" in condition:
        sats, report = [], []
        for i, sub in enumerate(condition["any"]):
            s, r = evaluate(sub, view, f"{path}any[{i}].")
            sats.append(s)
            report.extend(r)
        return (max(sats) if sats else 0.0), report
    if "not" in condition:
        s, r = evaluate(condition["not"], view, f"{path}not.")
        return 1.0 - s, r
    sat, detail = _eval_leaf(condition, view)
    return sat, [{"path": path.rstrip("."), "type": condition.get("type"),
                  "satisfaction": round(sat, 4), "detail": detail}]


def is_satisfied(condition: dict, view: SubjectiveView) -> bool:
    return evaluate(condition, view)[0] >= 1.0


def flatten_leaves(condition: dict | None) -> list[dict]:
    """The leaf predicates of a condition, in declaration order.
    `any` branches contribute their FIRST leaf only (a plan or a
    candidate set pursues one alternative at a time); `not` leaves
    are skipped (nothing to pursue toward a negation)."""
    if not condition:
        return []
    if "all" in condition:
        out: list[dict] = []
        for sub in condition["all"]:
            out.extend(flatten_leaves(sub))
        return out
    if "any" in condition:
        subs = condition["any"]
        return flatten_leaves(subs[0]) if subs else []
    if "not" in condition:
        return []
    return [condition]


def all_leaves(condition: dict | None) -> list[dict]:
    """Every leaf including all `any` alternatives (progress math)."""
    if not condition:
        return []
    if "all" in condition or "any" in condition:
        key = "all" if "all" in condition else "any"
        out: list[dict] = []
        for sub in condition[key]:
            out.extend(all_leaves(sub))
        return out
    if "not" in condition:
        return all_leaves(condition["not"])
    return [condition]


def progress_of(condition: dict | None, view: SubjectiveView) -> float:
    """Section 4.3 progress, computed RECURSIVELY over the condition
    tree: an `all` node contributes the mean of its children's
    progress (so a nested `any` counts once, at its best branch), an
    `any` node the max of its children, a leaf its own satisfaction.
    Distinct from evaluate()'s satisfaction (an `all` is satisfied
    only when its weakest leaf is): progress is the soft measure,
    satisfaction the hard one. No condition -> 0."""
    if not condition:
        return 0.0
    if "all" in condition:
        subs = condition["all"]
        return (sum(progress_of(s, view) for s in subs) / len(subs)
                if subs else 1.0)
    if "any" in condition:
        subs = condition["any"]
        return max((progress_of(s, view) for s in subs), default=0.0)
    if "not" in condition:
        return 1.0 - progress_of(condition["not"], view)
    return evaluate(condition, view)[0]


def references(condition: dict | None) -> set[tuple[str, str]]:
    """The (kind, name) references a condition reads — objects,
    beliefs, locations, agents — used by the Plan Engine's
    relevant-environment-change reset (design section 5.4)."""
    refs: set[tuple[str, str]] = set()
    for leaf in all_leaves(condition):
        t = leaf.get("type")
        if t in ("object_possession", "object_at_location", "object_found"):
            refs.add(("object", leaf.get("object")))
        elif t == "belief_threshold":
            refs.add(("belief", leaf.get("belief")))
        elif t == "agent_at_location":
            refs.add(("location", leaf.get("location")))
            refs.add(("agent", leaf.get("agent")))
        elif t == "trust_threshold":
            refs.add(("agent", leaf.get("other")))
        elif t == "event_occurred":
            for f in ("claim", "topic", "object", "location"):
                if leaf.get(f):
                    refs.add((f, leaf.get(f)))
            if leaf.get("actor"):
                refs.add(("agent", leaf.get("actor")))
        elif t == "goal_status":
            refs.add(("goal", leaf.get("goal")))
    return refs


def validate_subjective(condition: dict | None, where: str = "condition") -> None:
    """Load-time contract check: a decision-path condition must be
    built from the closed vocabulary and must not declare the
    objective domain. Raises ValueError naming the culprit."""
    if condition is None:
        return
    if not isinstance(condition, dict):
        raise ValueError(f"{where}: condition must be a dict")
    if condition.get("domain") == "objective":
        raise ValueError(f"{where}: objective domain is not allowed "
                         "in decision-path conditions")
    for key in ("all", "any"):
        if key in condition:
            for i, sub in enumerate(condition[key]):
                validate_subjective(sub, f"{where}.{key}[{i}]")
            return
    if "not" in condition:
        validate_subjective(condition["not"], f"{where}.not")
        return
    known = {"object_possession", "object_at_location", "object_found",
             "belief_threshold", "agent_at_location", "event_occurred",
             "trust_threshold", "goal_status"}
    if condition.get("type") not in known:
        raise ValueError(f"{where}: unknown predicate type "
                         f"{condition.get('type')!r} (closed vocabulary)")
