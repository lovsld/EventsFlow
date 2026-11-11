"""Prediction Engine (Phase C, design section 4.3).

The predictive-processing half of the cognitive loop, as two pure,
deterministic operations:

  predict(agent, context, decision) -> Prediction
      Called at decision time, BEFORE the action is executed. The
      prediction is computed from the DecisionContext snapshot (who is
      co-located, trust in the target, current beliefs) plus the chosen
      action — never from a provider's free-text reasoning (task-book
      constraint 12). Fields are chosen so each one can be checked
      against the execution result and the next perception:
        target_present          speech/show target is in reach
        action_success          the Resolver will accept the action
        refusal_expected        ask/confront will be refused
                                (expected only when trust is very low)
        new_information_expected the action will bring back information
                                (a reply, an observation, a find)
        found_expected          a search will find the object

  compare(prediction, action_result, next_perception) -> PredictionError
      Called when the action result arrives. Produces the structured
      mismatch list and a weighted `surprise` in [0, 1]. compare() is
      pure bookkeeping: it changes no agent state. Turning the error
      into belief/trust movement is BeliefManager's job.

Actual-value definitions (fixed, so tests can assert them):
  action_success  := action_result["ok"]
  target_present  := False iff the result reason is target_left /
                     target_not_here, True iff the action succeeded
  refusal         := the next perception contains a `refuse` event
                     whose actor is the prediction's target
  new_information := search: the result found something;
                     speak/ask/confront: the target uttered something
                     (speak/ask/confront/refuse) in the next perception;
                     observe/listen: the next perception holds at least
                     one non-ambient event by somebody else;
                     anything else: False
  found           := bool(action_result["found"])
"""
from __future__ import annotations

from .protocol import AgentDecision, DecisionContext, Prediction, PredictionError

SPEECH = ("speak", "ask", "confront")
WEIGHTS = {"target_present": 0.4, "action_success": 0.3, "refusal": 0.2,
           "new_information": 0.1, "found": 0.4}
# An agent expects a refusal only from someone it barely trusts.
REFUSAL_TRUST_THRESHOLD = 0.25


class PredictionEngine:
    # ---------------- generate (decision time) ----------------
    def predict(self, agent, context: DecisionContext,
                decision: AgentDecision) -> Prediction:
        a = decision.action
        colocated = [c["id"] for c in context.perception.get("colocated", [])]
        trust_map: dict = getattr(context, "trust", {}) or {}
        target = a.target
        target_present = (target in colocated) if target else None

        if a.type == "move":
            # V0.2-B threading: adjacency from the run's Scenario
            # Definition instance (carried on the agent), not a
            # module-level default graph.
            success = a.target_location in agent.scenario.adjacency.get(
                context.location, [])
        elif a.type in SPEECH:
            success = bool(target_present) if target else True
        elif a.type == "show":
            success = bool(target_present) if target else False
        else:
            success = True

        refusal = None
        if a.type in ("ask", "confront") and target:
            refusal = trust_map.get(target, 0.3) < REFUSAL_TRUST_THRESHOLD

        new_info = a.type in ("observe", "listen", "search", "ask", "confront") \
            or (a.type == "speak" and target is not None)

        found = None
        if a.type == "search":
            place = a.target_location or context.location
            # V0.2-B: the found expectation is subjective and generic —
            # the place affords searching (scenario scenery) and this
            # agent's own searches there have not already come up empty
            # nor produced its findings. (Pre-B this was hardcoded to
            # the letter in the back room.)
            found = agent.scenario.searchable_at(place) \
                and place not in agent.state.searched_empty_places

        return Prediction(
            prediction_id=f"pred_{context.character_id}_{context.tick}",
            character_id=context.character_id, tick=context.tick,
            action_type=a.type, target=target,
            target_present=target_present, action_success=success,
            refusal_expected=refusal, new_information_expected=new_info,
            found_expected=found,
            basis={"location": context.location, "colocated": colocated,
                   "trust_in_target": trust_map.get(target) if target else None,
                   "perceived_ids": sorted(agent.state.perceived_event_ids)})

    # ---------------- evaluate (result time) ----------------
    def compare(self, prediction: Prediction, action_result: dict | None,
                next_perception: dict | None) -> PredictionError:
        details: dict[str, dict] = {}
        mismatches: list[str] = []

        def check(field: str, predicted, actual) -> None:
            if predicted is None or actual is None:
                return
            details[field] = {"predicted": predicted, "actual": actual}
            if predicted != actual:
                mismatches.append(field)

        if action_result is not None:
            check("action_success", prediction.action_success,
                  bool(action_result.get("ok")))
            if prediction.target is not None \
                    and prediction.action_type in SPEECH + ("show",):
                reason = action_result.get("reason")
                if reason in ("target_left", "target_not_here"):
                    actual_present = False
                elif action_result.get("ok"):
                    actual_present = True
                else:
                    actual_present = None
                check("target_present", prediction.target_present, actual_present)
            if prediction.found_expected is not None:
                check("found", prediction.found_expected,
                      bool(action_result.get("found")))

        if next_perception is not None:
            if prediction.refusal_expected is not None:
                actual_refusal = any(
                    ev.get("type") == "refuse" and ev.get("actors")
                    and ev["actors"][0] == prediction.target
                    for ev in next_perception.get("events", []))
                check("refusal", prediction.refusal_expected, actual_refusal)
            check("new_information", prediction.new_information_expected,
                  self._new_information(prediction, action_result, next_perception))

        denom = sum(WEIGHTS[f] for f in details)
        surprise = round(sum(WEIGHTS[f] for f in mismatches) / denom, 3) \
            if denom else 0.0
        return PredictionError(
            prediction_id=prediction.prediction_id,
            character_id=prediction.character_id,
            decision_tick=prediction.tick,
            evaluated_tick=(next_perception or {}).get("tick", prediction.tick),
            mismatches=mismatches, surprise=surprise, details=details)

    @staticmethod
    def _new_information(prediction: Prediction, action_result: dict | None,
                         next_perception: dict | None) -> bool:
        at = prediction.action_type
        if at == "search":
            return bool(action_result and action_result.get("found"))
        events = (next_perception or {}).get("events", [])
        if at in SPEECH:
            return any(ev.get("type") in SPEECH + ("refuse",)
                       and ev.get("actors")
                       and ev["actors"][0] == prediction.target
                       for ev in events)
        if at in ("observe", "listen"):
            return any(ev.get("type") != "ambient"
                       and prediction.character_id not in (ev.get("actors") or [])
                       for ev in events)
        return False
