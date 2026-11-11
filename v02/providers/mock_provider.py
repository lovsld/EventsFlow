"""Mock Provider — deterministic, no external calls. For tests only.

Behaviour is deliberately simple but context-driven (it reads co-location,
pending questions and beliefs from the DecisionContext), so integration
tests exercise the real runtime/world path.
"""
from __future__ import annotations

import random

from ..protocol import ActionSpec, AgentDecision, DecisionContext, ProviderResult
from .base import LLMProvider


class MockProvider(LLMProvider):
    name = "mock"
    model = "mock-1"

    def __init__(self, config, seed: int = 42):
        super().__init__(config, seed)
        self.rng = random.Random(seed + config.seed_offset)
        self.calls = 0

    async def generate_decision(self, context: DecisionContext) -> ProviderResult:
        self.calls += 1
        colocated = [c["id"] for c in context.perception.get("colocated", [])]
        pending = getattr(context, "pending_question", None)
        beliefs = {b.key: b for b in context.beliefs}
        me = self.config.character_id

        if pending and colocated:
            decision = AgentDecision(
                intent="answer_question",
                target=pending["from"],
                action=ActionSpec(type="speak", target=pending["from"],
                                  claim="denial" if me == "owner" else "smalltalk",
                                  topic=pending.get("topic"),
                                  content=f"{self.config.name}：（mock 回应）这件事我需要再想想。"),
                reasoning_summary="mock: 有人正在向我提问，先回应。",
                expected_outcome="questioner_reacts", confidence=0.6)
        elif me == "soldier" and beliefs.get("anonymous_letter_exists") and \
                beliefs["anonymous_letter_exists"].kind == "experienced" and "owner" in colocated:
            decision = AgentDecision(
                intent="confront_about_letter", target="owner",
                action=ActionSpec(type="confront", target="owner", claim="letter_discussion",
                                  topic="letter_discussion",
                                  content=f"{self.config.name}：那封信，我已经看过了。"),
                reasoning_summary="mock: 我已亲读匿名信，当面对质。",
                expected_outcome="owner_may_reveal_information", confidence=0.8)
        elif colocated:
            target = colocated[0]
            decision = AgentDecision(
                intent="greet", target=target,
                action=ActionSpec(type="speak", target=target, claim="greeting",
                                  topic="greeting",
                                  content=f"{self.config.name}：晚上好，雨夜里酒馆可是个好去处。"),
                reasoning_summary="mock: 有人在场，打个招呼。",
                expected_outcome="conversation_continues", confidence=0.5)
        elif me == "soldier" and context.location != "back_room" and \
                context.emotional_state.get("suspicion", 0) > 0.5:
            dest = "tables" if context.location == "bar" else "back_room" \
                if context.location == "tables" else "tables"
            decision = AgentDecision(
                intent="move_to_search", target=None,
                action=ActionSpec(type="move", target_location=dest),
                reasoning_summary="mock: 怀疑上升，去后屋方向看看。",
                expected_outcome="find_evidence", confidence=0.55)
        elif context.location == "back_room" and me == "soldier":
            decision = AgentDecision(
                intent="search_drawer", target=None,
                action=ActionSpec(type="search", target_location="back_room"),
                reasoning_summary="mock: 在后屋，搜查抽屉。",
                expected_outcome="find_evidence", confidence=0.6)
        else:
            decision = AgentDecision(
                intent="observe", target=None,
                action=ActionSpec(type="observe"),
                reasoning_summary="mock: 先观察。",
                expected_outcome="learn_more", confidence=0.4)
        return ProviderResult(decision=decision, provider=self.name, model=self.model,
                              raw_output=decision.model_dump_json())
