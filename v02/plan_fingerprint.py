"""V0.2-C C4: semantic Plan Fingerprints + context digests (design 5.3).

The replan gate's two pure questions — "is this new plan the SAME
plan that already failed?" and "is the world, as this agent sees
it, the SAME world it failed in?" — are answered here, as pure
functions over serializable shapes, so they can be unit-tested
and recomputed offline from plan_transitions snapshots (the
§八 Repeated Plan Fingerprint Count is defined over exactly
these functions).

Fingerprint: (goal_id, steps_sig) where steps_sig is the
MULTISET of step signatures. A step signature is (the set of its
success condition's NORMALIZED LEAF SIGNATURES, the set of its
template verbs). Leaf signatures normalize away everything that
is wording, not semantics: goal text, step_id and objective text
never enter; agent references collapse to self/other; numeric
thresholds are bucketed (confidence/trust by 0.25), so an LLM's
phrasing jitter — or a re-synthesis with a cosmetically different
threshold — does not mint a "different" plan. Similarity is the
multiset Jaccard over step signatures; a different goal is never
similar.

Context digest: a canonical JSON string over the subjective
facts the goal's success/activation conditions REFERENCE —
related belief confidences bucketed at 0.1, holdings, found
objects, the derived searched-places set, and the agent's own
location. Two digests are equal iff none of those facts changed
— which is exactly the gate's "same world" test, and exactly
why warehouse-style productive replans survive it: between a
failed search plan and its re-proposal, the searched set and
the location have usually moved.
"""
from __future__ import annotations

import json
from collections import Counter

from . import conditions
from .protocol import PlanStep, wrap_step

SIMILARITY_THRESHOLD = 0.8
GATE_SOFT_CAP = 3
MEMORY_CAP = 8

_THRESHOLD_BUCKET = 0.25
_BELIEF_BUCKET = 0.1


# ---------------- leaf / step signatures ----------------

def _agent_ref(value, agent_id: str) -> str:
    if value is None:
        return "-"
    if value in ("self", agent_id):
        return "self"
    if value in ("any_other", "any"):
        return str(value)
    return "other"


def _threshold_bucket(value, default: float = 1.0) -> int:
    try:
        return int(float(value) / _THRESHOLD_BUCKET)
    except (TypeError, ValueError):
        return int(default / _THRESHOLD_BUCKET)


def leaf_signature(leaf: dict, agent_id: str) -> tuple:
    """(leaf type, normalized reference) — see the module docstring
    for the normalization rules."""
    t = leaf.get("type")
    if t == "belief_threshold":
        tier = leaf.get("min_source_tier")
        tier_sig = -1
        if tier is not None:
            try:
                tier_sig = conditions.tier_of(tier)
            except Exception:
                tier_sig = -2
        return (t, leaf.get("belief"),
                _threshold_bucket(leaf.get("min_confidence", 1.0)),
                tier_sig)
    if t == "trust_threshold":
        return (t, _agent_ref(leaf.get("other"), agent_id),
                _threshold_bucket(leaf.get("min_trust", 1.0)))
    if t == "object_possession":
        return (t, leaf.get("object"),
                _agent_ref(leaf.get("holder", "self"), agent_id))
    if t == "object_found":
        return (t, leaf.get("object"))
    if t == "object_at_location":
        return (t, leaf.get("object"), leaf.get("location"))
    if t == "agent_at_location":
        return (t, _agent_ref(leaf.get("agent", "self"), agent_id),
                leaf.get("location"))
    if t == "event_occurred":
        return (t, leaf.get("event_type", leaf.get("type_")),
                _agent_ref(leaf.get("actor"), agent_id),
                _agent_ref(leaf.get("target"), agent_id),
                leaf.get("claim"), leaf.get("topic"),
                leaf.get("object"), leaf.get("location"),
                int(leaf.get("count", 1) or 1))
    if t == "goal_status":
        return (t, leaf.get("goal"), leaf.get("status"))
    return (str(t),)


def step_signature(step, agent_id: str) -> tuple:
    """(frozenset of leaf signatures, frozenset of template verbs).
    A legacy step (no success condition) signs as (empty, verbs) —
    free text carries no semantics to distinguish plans by, which
    is an honest limit of fingerprinting legacy plans."""
    step = wrap_step(step, 0) if not isinstance(step, PlanStep) else step
    leaves = frozenset(
        leaf_signature(leaf, agent_id)
        for leaf in conditions.all_leaves(step.success_condition))
    verbs = frozenset(
        tmpl.get("type") for tmpl in step.action_candidates
        if isinstance(tmpl, dict) and tmpl.get("type"))
    return (leaves, verbs)


def _sig_to_json(sig: tuple) -> list:
    leaves, verbs = sig
    return [[list(leaf) for leaf in sorted(leaves, key=repr)],
            sorted(verbs)]


def _sig_from_json(data: list) -> tuple:
    leaves, verbs = data
    return (frozenset(tuple(leaf) for leaf in leaves),
            frozenset(verbs))


def plan_fingerprint(steps, goal_id: str | None, agent_id: str) -> dict:
    """The serializable fingerprint of a proposed/existing plan:
    {goal_id, steps: [[step_sig_json, count], ...]} with the
    step-signature multiset in canonical (sorted) order."""
    counter: Counter = Counter()
    for step in steps or []:
        counter[step_signature(step, agent_id)] += 1
    return {"goal_id": goal_id,
            "steps": [[_sig_to_json(sig), n]
                      for sig, n in sorted(counter.items(),
                                           key=lambda kv: repr(kv[0]))]}


def fingerprint_similarity(a: dict, b: dict) -> float:
    """Multiset Jaccard over step signatures; different goals are
    never similar (0.0). Two empty plans for the same goal are
    identical (1.0)."""
    if a.get("goal_id") != b.get("goal_id"):
        return 0.0
    ca = Counter({_sig_from_json(sig): n for sig, n in a.get("steps", [])})
    cb = Counter({_sig_from_json(sig): n for sig, n in b.get("steps", [])})
    if not ca and not cb:
        return 1.0
    inter = sum((ca & cb).values())
    union = sum((ca | cb).values())
    return inter / union if union else 0.0


# ---------------- context digest ----------------

def context_digest(view: conditions.SubjectiveView, goal) -> str:
    """Canonical digest of the subjective facts the goal's
    success/activation conditions reference (design 5.3). All
    inputs are the evaluating agent's own state — the digest is
    a summary of "the world as I saw it", never of the world."""
    refs: set = set()
    if goal is not None:
        refs |= conditions.references(
            getattr(goal, "success_condition", None))
        refs |= conditions.references(
            getattr(goal, "activation_condition", None))
    beliefs = {}
    for kind, name in sorted(refs):
        if kind == "belief":
            conf = view.belief_conf(name)
            beliefs[name] = max(0, min(10, int(conf / _BELIEF_BUCKET)))
    payload = {"beliefs": beliefs,
               "holdings": sorted(view.holdings),
               "found": sorted(view.found_objects),
               "searched": sorted(view.searched_places.keys()),
               "location": view.location}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


# ---------------- the gate's pure judgement ----------------

def match_failure(memory: list[dict], fingerprint: dict,
                  digest: str) -> tuple[dict, float] | None:
    """The gate judgement (design 5.3): the first memory entry
    whose plan is similar (>= SIMILARITY_THRESHOLD) to the
    proposed fingerprint AND whose recorded context digest is
    identical to the current one — same plan, same world.
    Returns (entry, similarity) or None."""
    for entry in memory or []:
        if entry.get("context_digest") != digest:
            continue
        sim = fingerprint_similarity(entry.get("fingerprint") or {},
                                     fingerprint)
        if sim >= SIMILARITY_THRESHOLD:
            return entry, sim
    return None
