"""Phase 1 provider fix: the Phase 6 LLM runs lost 14/60
decisions (23%) to extra keys inside plan_update (desc / step /
actor) rejected by the extra=forbid schema. _sanitize must now
strip undeclared keys so those decisions validate; genuinely
invalid output must still fail."""
from __future__ import annotations

import json

import pytest

from v02.protocol import AgentDecision
from v02.providers.gemini_provider import GeminiProvider
from v02.providers.openai_provider import OpenAICompatibleProvider

FAILED_SHAPE = {
    "intent": "移动到酒馆主厅",
    "target": "bar",
    "action": {"type": "move", "target_location": "bar",
               "bogus_field": 1},
    "reasoning_summary": "桌边无人，酒吧更可能有人。",
    "expected_outcome": "到达主厅",
    "confidence": 0.7,
    "selected_goal_id": None,
    "plan_update": {"status": "replace",
                    "desc": "移动到酒馆主厅",
                    "step": "向老板打听行情",
                    "actor": "self",
                    "new_goal": "了解商业环境",
                    "new_steps": [{"step_id": "s1",
                                   "objective": "去主厅",
                                   "extra": "x"}]},
}


@pytest.mark.parametrize("sanitize", [
    OpenAICompatibleProvider._sanitize,
    GeminiProvider._sanitize])
def test_extra_keys_are_stripped_and_decision_validates(sanitize):
    raw = json.dumps(FAILED_SHAPE, ensure_ascii=False)
    decision = AgentDecision.model_validate(sanitize(raw))
    assert decision.action.type == "move"
    assert decision.plan_update.status == "replace"


@pytest.mark.parametrize("sanitize", [
    OpenAICompatibleProvider._sanitize,
    GeminiProvider._sanitize])
def test_semantically_invalid_output_still_fails(sanitize):
    bad = dict(FAILED_SHAPE, action={"type": "teleport"})
    raw = json.dumps(bad, ensure_ascii=False)
    with pytest.raises(Exception):
        AgentDecision.model_validate(sanitize(raw))
