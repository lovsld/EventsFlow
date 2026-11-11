"""Belief Manager — prediction-error-driven adjustment (Phase C).

Perception-driven belief updates stay where Phase A put them (the
agent's interpretation path over AgentMemory, with its three epistemic
kinds and merge rule). This component owns exactly one thing: turning a
structured PredictionError into belief / trust / emotion movement, by
an explicit rule table that tests can assert direction and magnitude
against. It applies ONLY on mismatches — a prediction that hits
changes nothing (no phantom adjustment).

Rule table (deltas are exact):
  target_present missed (target slipped away mid-tick):
      trust[target] -= 0.05 ; suspicion += 0.05
  action_success missed for any other reason:
      trust[target] -= 0.03 (or suspicion += 0.03 when targetless)
      — skipped when target_present already missed (same root cause,
        no double penalty)
  refusal missed, predicted engagement but the target refused:
      trust[target] -= 0.08 ; belief "<target>_cooperative" -> 0.25
      (kind inferred, via the standard merge rule)
  refusal missed, predicted refusal but the target engaged:
      trust[target] += 0.04 ; belief "<target>_cooperative" -> 0.65
  new_information / found missed:
      recorded in the error only — absence (or unexpected presence)
      of information is not evidence about anyone, so it moves no
      belief and no trust; it still feeds `surprise` and therefore
      memory-consolidation importance.

Every effect is recorded on the PredictionError object (belief_effects
/ trust_effects / emotion_effects with before/after) before this
returns, so the persisted error is the complete audit of the change.
"""
from __future__ import annotations

from .protocol import Prediction, PredictionError


class BeliefManager:
    def apply_prediction_error(self, agent, prediction: Prediction,
                               error: PredictionError) -> PredictionError:
        memory = agent.memory
        st = agent.state
        target = prediction.target
        mismatches = set(error.mismatches)

        def trust_adj(other: str, delta: float, cause: str) -> None:
            before = memory.trust_in(other)
            memory.adjust_trust(other, delta)
            error.trust_effects.append(
                {"other": other, "before": before,
                 "after": memory.trust_in(other), "delta": delta,
                 "cause": cause})

        def emotion_adj(name: str, delta: float, cause: str) -> None:
            before = st.emotions.get(name, 0.0)
            st.emotions[name] = round(min(1.0, max(0.0, before + delta)), 3)
            error.emotion_effects.append(
                {"emotion": name, "before": before, "after": st.emotions[name],
                 "delta": delta, "cause": cause})

        def belief_set(key: str, confidence: float, desc: str, cause: str) -> None:
            old = memory.belief(key)
            before = old.confidence if old else None
            # V0.2-A: the adjustment is an inference drawn from THIS
            # prediction's outcome — one evidence entry, keyed to the
            # prediction so a replay can never double-count it.
            memory.update_belief(
                key, confidence, "inferred", desc,
                source_type="inference",
                event_id=f"pred:{prediction.prediction_id}",
                tick=getattr(agent, "_tick", prediction.tick))
            error.belief_effects.append(
                {"key": key, "before": before,
                 "after": memory.belief(key).confidence,
                 "kind": "inferred", "cause": cause})

        if "target_present" in mismatches and target:
            trust_adj(target, -0.05, "target_present_miss")
            emotion_adj("suspicion", +0.05, "target_present_miss")

        if "action_success" in mismatches and "target_present" not in mismatches:
            if target:
                trust_adj(target, -0.03, "action_failed")
            else:
                emotion_adj("suspicion", +0.03, "action_failed")

        if "refusal" in mismatches and target:
            detail = error.details.get("refusal", {})
            key = f"{target}_cooperative"
            if detail.get("predicted") is False and detail.get("actual") is True:
                trust_adj(target, -0.08, "unexpected_refusal")
                belief_set(key, 0.25, f"{target} 拒绝继续沟通，合作意愿存疑",
                           "unexpected_refusal")
            elif detail.get("predicted") is True and detail.get("actual") is False:
                trust_adj(target, +0.04, "expected_refusal_but_engaged")
                belief_set(key, 0.65, f"{target} 愿意继续沟通",
                           "expected_refusal_but_engaged")

        return error
