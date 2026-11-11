"""Memory Manager — consolidation (Phase C, design section 4.3).

Episodic entries land in short_term_memory as the agent perceives the
world (AgentMemory.remember, Phase A semantics unchanged). When an
action result is evaluated, consolidate() scores every not-yet-scored
entry and promotes the important ones into long_term_memory:

  importance = kind_base
             + SURPRISE_BONUS * prediction_error.surprise
               (only for entries formed in the evaluated decision's
                window: decision tick .. evaluation tick)

  kind_base: experienced 0.5 / heard 0.35 / inferred 0.25

The prediction-error link is the point: an episode that disproved what
the agent expected is remembered; routine episodes mostly are not.
Threshold 0.65 — so an experienced episode consolidates iff the
accompanying surprise was >= 0.3, a heard one iff >= 0.6, an inferred
one iff >= 0.8. The scoring is a pure function of (entry, surprise),
fully deterministic, and the complete scored list is returned (and
persisted) — consolidation is auditable, not a black box.
"""
from __future__ import annotations

from .protocol import Prediction, PredictionError

BASE_IMPORTANCE = {"experienced": 0.5, "heard": 0.35, "inferred": 0.25}
SURPRISE_BONUS = 0.5
CONSOLIDATION_THRESHOLD = 0.65


class MemoryManager:
    def consolidate(self, agent, prediction: Prediction,
                    error: PredictionError, eval_tick: int) -> dict:
        st = agent.state
        start = agent._cons_from
        fresh = st.short_term_memory[start:]
        scored: list[dict] = []
        for e in fresh:
            importance = BASE_IMPORTANCE.get(e["kind"], 0.25)
            if prediction.tick <= e["tick"] <= eval_tick:
                importance += SURPRISE_BONUS * error.surprise
            importance = round(importance, 3)
            rec = {"tick": e["tick"], "kind": e["kind"], "text": e["text"],
                   "event_id": e.get("event_id"), "importance": importance,
                   "consolidated": importance >= CONSOLIDATION_THRESHOLD}
            scored.append(rec)
            if rec["consolidated"]:
                st.long_term_memory.append(
                    {"tick": e["tick"], "kind": e["kind"], "text": e["text"],
                     "event_id": e.get("event_id"), "importance": importance,
                     "consolidated_at_tick": eval_tick})
        agent._cons_from = len(st.short_term_memory)
        return {"character_id": st.character_id, "tick": eval_tick,
                "decision_key": f"{st.character_id}:{prediction.tick}",
                "prediction_id": prediction.prediction_id,
                "surprise": error.surprise, "entries": scored,
                "consolidated": [s for s in scored if s["consolidated"]]}
