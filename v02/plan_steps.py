"""Plan-step predicates — the shared definition of what it means for
an action to be consistent with (and to fulfil) a plan step.

This table began as the step-1 audit's M1 predicate table (it lived in
v02/audit.py). The plan-layer upgrade promoted it to a runtime module:
the audit and the runtime now use ONE definition, so "the action
matches the current step" means exactly the same thing when the
runtime advances a plan and when the audit later scores the run.
The semantics of step_consistent() are unchanged by the move — only
its home changed.

Two different questions are answered here, deliberately kept apart:

  step_consistent(step, action, ...) -> True / False / None
      The audit question: was this action consistent with that step?
      None means "unjudgeable here" (placeholder step, unknown label,
      or a step already satisfied at the agent's location — the audit
      excludes those from its denominators).

  step_fulfilled(step, action, action_ok, ...) -> bool
      The runtime question: did this EXECUTED action complete the
      step? A step is completed by a fact about the world — an action
      that was actually carried out (its result says ok) and that the
      predicate judges consistent with the step. A refused or
      target_left action fulfils nothing, however well-aimed it was.
      The one position case: return_to_front while the agent is
      already at the front is "moot" for the audit (None), but for
      execution the step's demand — be at the front — already holds,
      so the step counts as fulfilled.
"""
from __future__ import annotations

from collections import deque

from .context_builder import ADJACENCY

FRONT = {"bar", "tables"}
PLACEHOLDER_STEPS = {"decide_next_action"}


def action_signature(action: dict) -> str:
    """The agent runtime's own action signature format (same string
    the Plan Manager's step dedup compares)."""
    return (f"{action.get('type')}:"
            f"{action.get('target') or action.get('target_location') or ''}:"
            f"{action.get('topic') or action.get('object') or ''}")


def _distances_to(targets: set[str]) -> dict[str, int]:
    dist = {t: 0 for t in targets}
    q = deque(targets)
    while q:
        cur = q.popleft()
        for nb in ADJACENCY.get(cur, set()):
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                q.append(nb)
    return dist


def _toward(action: dict, loc: str, targets: set[str]) -> bool:
    """True when a move's destination is a next hop on a shortest path
    from loc toward the target set."""
    if action.get("type") != "move":
        return False
    dest = action.get("target_location")
    if dest not in ADJACENCY.get(loc, set()):
        return False
    dist = _distances_to(targets)
    return dist.get(dest, 99) == dist.get(loc, 99) - 1


def step_consistent(step: str, action: dict, *, agent_loc: str,
                    owner_loc: str) -> bool | None:
    """Predicate table (audit M1; runtime fulfilment). Returns
    True/False, or None when the step carries no judgeable action
    signature here (placeholder, unknown label, or a step already
    satisfied at the agent's location)."""
    t = action.get("type")
    claim = action.get("claim")
    target = action.get("target")
    if step in PLACEHOLDER_STEPS:
        return None
    if step == "return_to_front":
        if agent_loc in FRONT:
            return None  # already at the front: the step is moot
        return _toward(action, agent_loc, FRONT)
    if step == "confront_owner":
        return t in ("confront", "ask") and target == "owner"
    if step == "observe_response":
        return t in ("observe", "listen")
    if step == "press_for_evidence":
        if t in ("ask", "confront", "search"):
            return True
        if t == "speak" and claim in ("letter_discussion", "letter_question",
                                      "mayor_hint"):
            return True
        return _toward(action, agent_loc, {"back_room"}) or \
            _toward(action, agent_loc, {owner_loc})
    if step == "probe_more_mayor_news":
        if t in ("ask", "listen", "observe"):
            return True
        return t in ("speak", "ask") and claim in (
            "mayor_hint", "mayor_late_payment", "answer_mayor")
    if step == "protect_wealth":
        return t == "work"
    if step == "assess_letter_rumor_value":
        return t in ("observe", "listen", "ask")
    if step == "find_letter_source":
        return t in ("ask", "confront", "observe", "listen")
    if step == "find_who_talked":
        return t in ("ask", "confront")
    if step == "stay_calm":
        return t in ("work", "observe", "listen", "wait")
    if step == "deny_and_observe":
        return (t == "speak" and claim == "denial") or \
            t in ("refuse", "observe", "listen")
    if step == "deny_letter":
        return (t == "speak" and claim == "denial") or t == "refuse"
    if step == "assess_damage":
        return t in ("observe", "listen")
    if step == "protect_tavern":
        return t == "work" or (t == "speak" and claim == "denial") \
            or t == "refuse"
    return None  # unknown label: unmeasurable, reported separately


def step_fulfilled(step: str, action: dict, action_ok: bool, *,
                   agent_loc: str, owner_loc: str | None) -> bool:
    """Runtime fulfilment: the action was executed (its result is ok)
    and is consistent with the step. See the module docstring for the
    return_to_front position case."""
    if not action_ok:
        return False
    verdict = step_consistent(step, action, agent_loc=agent_loc,
                              owner_loc=owner_loc)
    if verdict is not None:
        return verdict
    return step == "return_to_front" and agent_loc in FRONT


def owner_location_from_perception(perception: dict | None,
                                   agent_loc: str) -> str | None:
    """Where the deciding agent can place the owner at decision time,
    from its own perception only: co-location wins; otherwise the
    destination of the owner's most recent perceived move; otherwise
    unknown (None — the predicate's toward-the-owner branch then
    simply cannot fire). Deterministic: perception events are scanned
    in delivery order."""
    perc = perception or {}
    if any(c.get("id") == "owner" for c in perc.get("colocated", [])):
        return agent_loc
    loc = None
    for ev in perc.get("events", []):
        if ev.get("type") == "move" \
                and (ev.get("actors") or [None])[0] == "owner":
            loc = ev.get("to") or loc
    return loc
