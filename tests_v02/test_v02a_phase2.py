"""V0.2-A Phase 2 tests: protocol & validation hardening.

Covered: strict unknown-field rejection on the decision protocol
(extra="forbid"); selected_goal_id ownership validation at runtime;
available_actions membership validation for LLM decisions (the
step-1 audit's M4c check moved upstream); the LLM timeout becoming
environment-configurable. All providers here are stubs — no real API
is ever called.
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from v02.agent import CharacterAgent
from v02.config import load_all
from v02.protocol import (ActionSpec, AgentDecision, PlanUpdate,
                          ProviderResult)
from v02.providers import make_provider
from v02.providers.base import LLMProvider


def perc(tick=1, loc="tables", colocated=()):
    return {"tick": tick, "time": "21:05", "location": loc,
            "colocated": [{"id": c, "name": c, "role": ""} for c in colocated],
            "events": [], "muffled": [], "visible_objects": [],
            "agent_id": "x", "world_public": {"weather": "clear"}}


class StubLLM(LLMProvider):
    """A fake real-LLM provider: membership validation applies."""
    name = "stub-llm"
    model = "stub"
    validates_membership = True

    def __init__(self, config, decision, seed=42):
        super().__init__(config, seed)
        self._decision = decision

    async def generate_decision(self, context):
        return ProviderResult(decision=self._decision, provider=self.name,
                              model=self.model)


def make_agent(cid, decision):
    cfg = load_all()[cid]
    return CharacterAgent(cfg, StubLLM(cfg, decision))


# ---------- strict protocol: unknown fields are rejected ----------

def test_decision_rejects_unknown_top_level_field():
    with pytest.raises(ValidationError):
        AgentDecision.model_validate({
            "intent": "x", "action": {"type": "wait"},
            "backdoor": "rm -rf"})


def test_decision_rejects_unknown_action_field():
    with pytest.raises(ValidationError):
        AgentDecision.model_validate({
            "intent": "x",
            "action": {"type": "wait", "teleport_to": "back_room"}})


def test_decision_rejects_unknown_plan_update_field():
    with pytest.raises(ValidationError):
        AgentDecision.model_validate({
            "intent": "x", "action": {"type": "wait"},
            "plan_update": {"status": "continue", "override_world": True}})


def test_decision_accepts_selected_goal_id_field():
    d = AgentDecision(intent="x", action=ActionSpec(type="wait"),
                      selected_goal_id="protect_tavern")
    assert d.selected_goal_id == "protect_tavern"
    assert PlanUpdate(status="continue").status == "continue"


# ---------- selected_goal_id ownership ----------

def test_forged_selected_goal_id_is_rejected_and_recorded():
    decision = AgentDecision(intent="wait", action=ActionSpec(type="wait"),
                             selected_goal_id="no_such_goal")
    agent = make_agent("soldier", decision)
    out, record = asyncio.run(agent.decide(perc()))
    assert out is None
    assert "selected_goal_id" in record["error"]
    assert "no_such_goal" in record["error"]


def test_own_selected_goal_id_passes():
    decision = AgentDecision(intent="wait", action=ActionSpec(type="wait"),
                             selected_goal_id="complete_investigation")
    agent = make_agent("soldier", decision)
    out, record = asyncio.run(agent.decide(perc()))
    assert out is not None and record["error"] is None
    assert out.selected_goal_id == "complete_investigation"


def test_absent_selected_goal_id_passes():
    decision = AgentDecision(intent="wait", action=ActionSpec(type="wait"))
    agent = make_agent("soldier", decision)
    out, record = asyncio.run(agent.decide(perc()))
    assert out is not None and record["error"] is None


# ---------- available_actions membership (LLM only) ----------

def test_llm_action_outside_available_actions_is_blocked():
    # speaking to the owner, who is not here: not offered this tick
    decision = AgentDecision(
        intent="talk", action=ActionSpec(type="speak", target="owner",
                                         content="老板？"))
    agent = make_agent("soldier", decision)
    out, record = asyncio.run(agent.decide(perc(colocated=())))
    assert out is None
    assert "available_actions" in record["error"]


def test_llm_action_inside_available_actions_passes():
    decision = AgentDecision(
        intent="talk", action=ActionSpec(type="speak", target="owner",
                                         content="老板？"))
    agent = make_agent("soldier", decision)
    out, record = asyncio.run(agent.decide(perc(colocated=("owner",))))
    assert out is not None and record["error"] is None


def test_membership_flag_only_on_llm_providers():
    from v02.providers.gemini_provider import GeminiProvider
    from v02.providers.mock_provider import MockProvider
    from v02.providers.openai_provider import OpenAICompatibleProvider
    from v02.providers.rule_provider import RuleProvider
    assert GeminiProvider.validates_membership is True
    assert OpenAICompatibleProvider.validates_membership is True
    assert RuleProvider.validates_membership is False
    assert MockProvider.validates_membership is False


# ---------- timeout via environment ----------

def test_timeout_from_shared_env(monkeypatch):
    monkeypatch.delenv("ACP_GEMINI_TIMEOUT", raising=False)
    monkeypatch.delenv("ACP_DEEPSEEK_TIMEOUT", raising=False)
    monkeypatch.setenv("ACP_LLM_TIMEOUT", "12")
    cfg = load_all()["owner"]
    assert make_provider("llm", cfg, seed=42).timeout == 12.0
    assert make_provider("llm", cfg, seed=42,
                         provider="deepseek").timeout == 12.0


def test_timeout_backend_env_wins_and_default_unchanged(monkeypatch):
    monkeypatch.setenv("ACP_LLM_TIMEOUT", "12")
    monkeypatch.setenv("ACP_DEEPSEEK_TIMEOUT", "5")
    cfg = load_all()["owner"]
    assert make_provider("llm", cfg, seed=42,
                         provider="deepseek").timeout == 5.0
    monkeypatch.delenv("ACP_LLM_TIMEOUT")
    monkeypatch.delenv("ACP_DEEPSEEK_TIMEOUT")
    monkeypatch.delenv("ACP_GEMINI_TIMEOUT", raising=False)
    assert make_provider("llm", cfg, seed=42).timeout == 60.0
