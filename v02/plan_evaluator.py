"""V0.2-B B3: the Plan Step Evaluator — design section 5.3.

One component answers the runtime question "did the current plan
step get fulfilled / did it fail?" — and it answers ONLY from the
step's declared conditions, evaluated through conditions.py over
the agent's subjective view. There is no step-label table here and
no scenario literals: step fulfilment is a condition fact, exactly
as goal completion is. (This module replaces v02/plan_steps.py,
whose tavern step-label predicates were the last piece of scenario
semantics in generic code; the review's P0 — a label table deciding
fulfilment — is closed by deleting it.)

Evaluation contract (design 5.4), for the pending step after the
agent's latest action result has been ingested:

  fulfilled  the step has a success_condition and it is satisfied.
  failed     one CONSECUTIVE failure, counted only when ALL hold:
             (a) the action was consistent with the step (it matches
                 one of the step's action_candidates templates);
             (b) the success condition is not satisfied;
             (c) the failure condition is satisfied, OR the action
                 result says ok=False, OR (no failure condition AND
                 the success condition is decisively false — at
                 least one leaf at satisfaction exactly 0).
             An evaluation that is "not yet decidable" counts
             nothing and resets nothing.
  legacy     a step without a success condition (a wrapped free-text
             step) is never judged here at all: fulfilled=failed=
             False, and the caller advances it only on explicit
             provider advance.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import conditions
from .protocol import PlanStep

TEMPLATE_FIELDS = ("type", "target", "target_location", "object",
                   "claim", "topic")


def action_signature(action: dict) -> str:
    """The runtime's action signature format (type:target-or-place:
    topic-or-object) — used by dedup, audit and metrics alike."""
    return (f"{action.get('type')}:"
            f"{action.get('target') or action.get('target_location') or ''}:"
            f"{action.get('topic') or action.get('object') or ''}")


def action_matches_template(action: dict, template: dict) -> bool:
    """Field-level template match (design 5.1): every field the
    template specifies must equal the action's; unspecified fields
    are free. `topic` and `claim` name the same slot."""
    for f in TEMPLATE_FIELDS:
        if f in template:
            want = template[f]
            got = action.get("claim") if f == "topic" else action.get(f)
            if f == "claim":
                got = action.get("claim") or action.get("topic")
            if got != want:
                return False
    return True


def action_matches_templates(action: dict | None,
                             templates: list[dict]) -> bool:
    if not action or not templates:
        return False
    return any(action_matches_template(action, t) for t in templates)


@dataclass
class StepEvaluation:
    fulfilled: bool = False
    failed: bool = False
    consistent: bool = False
    legacy: bool = False
    terminal: bool = False
    reason: str = ""
    leaf_report: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"fulfilled": self.fulfilled, "failed": self.failed,
                "consistent": self.consistent, "legacy": self.legacy,
                "terminal": self.terminal, "reason": self.reason,
                "leaf_report": self.leaf_report}


def evaluate_step(step: PlanStep, *, action: dict | None,
                  action_result: dict | None,
                  view: conditions.SubjectiveView) -> StepEvaluation:
    """Judge the pending step after its action result was ingested.
    `view` is the agent's CURRENT subjective view (post-ingestion),
    so a find in the result is already reflected in holdings."""
    if step.success_condition is None:
        return StepEvaluation(legacy=True,
                              reason="legacy step: not condition-judged")
    ev = StepEvaluation()
    ev.consistent = action_matches_templates(action,
                                             step.action_candidates)
    sat, report = conditions.evaluate(step.success_condition, view)
    ev.leaf_report = report
    if sat >= 1.0:
        ev.fulfilled = True
        ev.reason = "success_condition satisfied"
        return ev
    # failure bookkeeping (design 5.4)
    if not ev.consistent:
        ev.reason = "action not consistent with the step: no count"
        return ev
    result_ok = (action_result or {}).get("ok", True)
    fail_sat = None
    if step.failure_condition is not None:
        fail_sat, _ = conditions.evaluate(step.failure_condition, view)
        if step.failure_condition.get("terminal") and fail_sat >= 1.0:
            ev.terminal = True
    decisive_false = any(leaf["satisfaction"] == 0.0 for leaf in report)
    if step.failure_condition is not None and fail_sat >= 1.0:
        ev.failed = True
        ev.reason = "failure_condition satisfied"
    elif not result_ok:
        ev.failed = True
        ev.reason = "action result ok=False"
    elif step.failure_condition is None and decisive_false:
        ev.failed = True
        ev.reason = "success_condition decisively false"
    else:
        ev.reason = "not yet decidable: no failure counted"
    return ev
