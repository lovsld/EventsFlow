"""V0.2-A Phase 4 tests: prompt-level information isolation
(task book section 13, group B) and the prompt-protocol snapshot.

Three agents in three epistemic states — knows first-hand / knows
nothing / knows by hearsay only — and the assertions run on the
actual prompt text each LLM provider would receive (system prompt +
serialized DecisionContext), cross-checked against the agents' final
states and the event-propagation records. Text-only checks would not
prove isolation; state-only checks would not prove the prompt is
clean. Both are asserted.
"""
from __future__ import annotations

from v02.agent import CharacterAgent
from v02.config import load_all
from v02.context_builder import build_context
from v02.providers import make_provider
from v02.providers.gemini_provider import GeminiProvider
from v02.providers.openai_provider import DeepSeekProvider

SOLDIER_PRIVATE_MEMORY = "我在后屋的抽屉里发现并读到一封匿名信"
LETTER_CONTENT = "私吞镇政府的钱"


def perc(tick, loc, events=()):
    return {"tick": tick, "time": "21:05", "location": loc,
            "colocated": [], "events": list(events), "muffled": [],
            "visible_objects": [], "agent_id": "x",
            "world_public": {"weather": "clear"}}


def make_agent(cid):
    cfg = load_all()[cid]
    return CharacterAgent(cfg, make_provider("mock", cfg, seed=42))


def prompt_text(agent, perception) -> str:
    """The full prompt an LLM would see for this agent right now:
    each real provider's system prompt + the serialized context."""
    ctx = build_context(agent, perception)
    parts = []
    for cls in (GeminiProvider, DeepSeekProvider):
        provider = cls(agent.config)
        parts.append(provider._system_prompt())
    parts.append(ctx.model_dump_json())
    return "\n".join(parts)


def prompt_beliefs(agent, perception) -> dict:
    """The beliefs exactly as serialized into the prompt context,
    keyed by belief key — kind assertions must be scoped per belief
    (an agent may legitimately hold OTHER first-hand beliefs)."""
    ctx = build_context(agent, perception)
    return {b.key: b for b in ctx.beliefs}


def knower():
    """The soldier, after finding and reading the letter himself."""
    agent = make_agent("soldier")
    agent.receive(perc(10, "back_room"), {"found": ["anonymous_letter"]})
    return agent


def hearer():
    """The merchant, after the soldier mentions the letter to him."""
    agent = make_agent("merchant")
    agent.state.location = "tables"
    ev = {"event_id": "E500", "tick": 3, "type": "speak",
          "actors": ["soldier"], "location": "tables",
          "claim": "letter_discussion", "topic": "letter_discussion",
          "text": "士兵：老板，我在后屋看见一封信。"}
    agent.receive(perc(3, "tables", events=[ev]), None)
    return agent


def test_knower_prompt_carries_first_hand_belief():
    agent = knower()
    text = prompt_text(agent, perc(11, "back_room"))
    assert "anonymous_letter_exists" in text
    shown = prompt_beliefs(agent, perc(11, "back_room"))
    assert shown["anonymous_letter_exists"].kind == "experienced"
    # state cross-check: the prompt reflects a real first-hand belief
    belief = agent.memory.belief("anonymous_letter_exists")
    assert belief.kind == "experienced" and belief.confidence == 1.0
    assert belief.evidence[0].source_type == "direct_observation"


def test_ignorant_prompt_has_no_letter_information():
    agent = make_agent("merchant")
    text = prompt_text(agent, perc(1, "tables"))
    assert "anonymous_letter_exists" not in text
    assert LETTER_CONTENT not in text
    # state cross-check: nothing to leak — no belief, no heard record
    assert agent.memory.belief("anonymous_letter_exists") is None
    assert agent.state.utterances_heard == []


def test_hearer_prompt_has_testimony_grade_only():
    agent = hearer()
    text = prompt_text(agent, perc(4, "tables"))
    assert "anonymous_letter_exists" in text
    shown = prompt_beliefs(agent, perc(4, "tables"))
    assert shown["anonymous_letter_exists"].kind == "heard"
    # the soldier's first-hand experience must NOT bleed into the
    # hearer's prompt: the letter belief is testimony-grade only,
    # and neither the soldier's private memory nor the letter's
    # content appears anywhere in the prompt text.
    # V0.2-B adjudication (parked item P-3): the confidence pin is
    # the design section 3.5 testimony formula value — base 1.0 x
    # (0.4 + 0.6 x trust 0.3) = 0.58; the old 0.7 pin is
    # arithmetically unreachable under that formula.
    assert shown["anonymous_letter_exists"].confidence == 0.58
    assert SOLDIER_PRIVATE_MEMORY not in text
    assert LETTER_CONTENT not in text
    # state cross-check: testimony-grade belief, event on record
    belief = agent.memory.belief("anonymous_letter_exists")
    assert belief.kind == "heard" and belief.confidence == 0.58
    assert belief.evidence[0].source_type == "testimony"
    assert belief.evidence[0].source == "soldier"
    assert belief.evidence[0].source_event_id == "E500"
    assert any(u["from"] == "soldier"
               and u["claim"] == "letter_discussion"
               for u in agent.state.utterances_heard)


def test_system_prompts_explain_goal_id_and_trust():
    """Phase 4 item 4-1 snapshot: both real providers' system prompts
    carry the selected_goal_id contract and explain that trust /
    relationships are the agent's own subjective view."""
    cfg = load_all()["owner"]
    for cls in (GeminiProvider, DeepSeekProvider):
        prompt = cls(cfg)._system_prompt()
        assert "selected_goal_id" in prompt
        assert "trust" in prompt and "主观认知" in prompt
