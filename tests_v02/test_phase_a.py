"""Phase A tests for ACP V0.2 (pytest).

Covered: protocol validation, provider interface & switching, OpenAI
provider failure honesty, plan-update gating, full-run information
isolation, divergent beliefs under the rule control group, decision
audit rows in SQLite, and the llm-mode preflight.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from v02.agent import CharacterAgent
from v02.config import load_all
from v02.persistence import Store
from v02.protocol import AgentDecision
from v02.providers import make_provider
from v02.providers.base import ProviderError
from v02.providers.openai_provider import OpenAIProvider
from v02.runner import preflight, run_simulation

ROOT = Path(__file__).resolve().parent.parent


# ---------- protocol ----------
def test_decision_schema_rejects_illegal_action_type():
    with pytest.raises(ValidationError):
        AgentDecision.model_validate({
            "intent": "hack", "action": {"type": "teleport_and_mind_read"}})


def test_decision_schema_accepts_book_example():
    d = AgentDecision.model_validate({
        "intent": "confront_owner", "target": "owner",
        "action": {"type": "speak", "content": "你为什么一直回避我的问题？"},
        "reasoning_summary": "老板一直回避后屋的问题。",
        "expected_outcome": "owner_may_reveal_information",
        "confidence": 0.78, "plan_update": {"status": "continue"}})
    assert d.action.type == "speak" and d.confidence == 0.78


# ---------- providers ----------
def test_openai_provider_without_key_fails_honestly(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = load_all()["owner"]
    provider = OpenAIProvider(cfg, seed=42)
    agent = CharacterAgent(cfg, provider)
    perc = {"tick": 1, "time": "21:05", "location": "bar", "colocated": [],
            "events": [], "muffled": [], "visible_objects": [],
            "agent_id": "owner", "world_public": {"weather": "clear"}}
    with pytest.raises(ProviderError):
        asyncio.run(provider.generate_decision(agent.last_context or
                                               _ctx(agent, perc)))


def _ctx(agent, perc):
    from v02.context_builder import build_context
    return build_context(agent, perc)


def test_llm_preflight_requires_key(monkeypatch):
    monkeypatch.setenv("ACP_LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        preflight("llm")
    preflight("rule")  # no key needed
    preflight("mock")


def test_gemini_provider_registered_and_credential_check():
    from v02.providers import make_provider
    from v02.providers.gemini_provider import GeminiProvider, credential_available
    cfg = load_all()["owner"]
    provider = make_provider("llm", cfg, seed=42)  # defaults to gemini backend
    assert isinstance(provider, GeminiProvider)
    # in this environment the connector credential is connected; the check
    # must at least return a bool without raising
    assert isinstance(credential_available(), bool)


def test_gemini_per_character_model_override(monkeypatch):
    from v02.providers import make_provider
    cfgs = load_all()
    monkeypatch.setenv("ACP_MODEL_SOLDIER", "gemini-2.5-flash")
    soldier = make_provider("llm", cfgs["soldier"], seed=42)
    assert soldier.model == "gemini-2.5-flash"
    owner = make_provider("llm", cfgs["owner"], seed=42)
    assert owner.model == "gemini-2.5-flash-lite"  # default when unset


def test_gemini_pacer_interval_per_model(monkeypatch):
    from v02.providers.gemini_provider import _interval_for
    monkeypatch.setenv("ACP_GEMINI_MIN_INTERVAL", "7")
    monkeypatch.setenv("ACP_GEMINI_MIN_INTERVAL_GEMINI_2_5_FLASH_LITE", "40")
    assert _interval_for("gemini-2.5-flash-lite") == 40.0
    assert _interval_for("gemini-2.5-flash") == 7.0


def test_gemini_credential_pool_rotates_on_429(monkeypatch):
    import asyncio
    import json as _json
    import v02.providers.gemini_provider as gp
    from v02.protocol import DecisionContext
    monkeypatch.setenv("ACP_GEMINI_CREDENTIALS", "custom.gemini, custom.gemini-2")
    monkeypatch.setenv("ACP_GEMINI_MIN_INTERVAL", "0")
    gp._EXHAUSTED_CREDENTIALS.clear()
    provider = gp.GeminiProvider(load_all()["owner"], seed=42)
    assert provider.credentials == ["custom.gemini", "custom.gemini-2"]
    decision = {"intent": "wait", "target": None,
                "action": {"type": "wait", "target": None, "target_location": None,
                           "object": None, "content": None, "claim": None,
                           "topic": None},
                "reasoning_summary": "pool test", "expected_outcome": "observe",
                "confidence": 0.5,
                "plan_update": {"status": "none", "reason": None,
                                "new_goal": None, "new_steps": []}}

    def fake_post(self, payload, credential=None):
        if credential == "custom.gemini":
            raise gp.ProviderError("HTTP 429 from Gemini API: quota")
        return {"candidates": [{"content": {"parts": [
            {"text": _json.dumps(decision)}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}

    monkeypatch.setattr(gp.GeminiProvider, "_post_generate", fake_post)
    ctx = DecisionContext(character_id="owner", tick=0, current_time="21:00",
                          location="bar", perception={}, active_goals=[])
    result = asyncio.run(provider.generate_decision(ctx))
    assert result.decision.action.type == "wait"
    assert "custom.gemini" in gp._EXHAUSTED_CREDENTIALS
    gp._EXHAUSTED_CREDENTIALS.clear()


def test_gemini_credential_pool_rotates_on_404(monkeypatch):
    # A key whose account cannot serve the model (404 "no longer available
    # to new users") must be skipped for the rest of the run, like a 429.
    import asyncio
    import json as _json
    import v02.providers.gemini_provider as gp
    from v02.protocol import DecisionContext
    monkeypatch.setenv("ACP_GEMINI_CREDENTIALS", "custom.gemini-4, custom.gemini-5")
    monkeypatch.setenv("ACP_GEMINI_MIN_INTERVAL", "0")
    gp._EXHAUSTED_CREDENTIALS.clear()
    provider = gp.GeminiProvider(load_all()["owner"], seed=42)
    decision = {"intent": "wait", "target": None,
                "action": {"type": "wait", "target": None, "target_location": None,
                           "object": None, "content": None, "claim": None,
                           "topic": None},
                "reasoning_summary": "pool 404 test", "expected_outcome": "observe",
                "confidence": 0.5,
                "plan_update": {"status": "none", "reason": None,
                                "new_goal": None, "new_steps": []}}

    def fake_post(self, payload, credential=None):
        if credential == "custom.gemini-4":
            raise gp.ProviderError("HTTP 404 from Gemini API: not available")
        return {"candidates": [{"content": {"parts": [
            {"text": _json.dumps(decision)}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}

    monkeypatch.setattr(gp.GeminiProvider, "_post_generate", fake_post)
    ctx = DecisionContext(character_id="owner", tick=0, current_time="21:00",
                          location="bar", perception={}, active_goals=[])
    result = asyncio.run(provider.generate_decision(ctx))
    assert result.decision.action.type == "wait"
    assert "custom.gemini-4" in gp._EXHAUSTED_CREDENTIALS
    gp._EXHAUSTED_CREDENTIALS.clear()


def test_provider_switch_same_agent_same_context():
    cfg = load_all()["merchant"]
    perc = {"tick": 1, "time": "21:05", "location": "bar",
            "colocated": [{"id": "owner", "name": "奥伦", "role": "酒馆老板"}],
            "events": [], "muffled": [], "visible_objects": [],
            "agent_id": "merchant", "world_public": {"weather": "clear"}}
    decisions = {}
    for mode in ("mock", "rule"):
        agent = CharacterAgent(cfg, make_provider(mode, cfg, seed=42))
        decision, record = asyncio.run(agent.decide(perc))
        assert decision is not None and record["error"] is None
        AgentDecision.model_validate(decision.model_dump())
        decisions[mode] = decision
    assert decisions["mock"].action.type in ("speak", "observe", "move", "search")


def test_plan_abandon_without_reason_is_rejected():
    cfg = load_all()["soldier"]
    agent = CharacterAgent(cfg, make_provider("mock", cfg, seed=42))
    agent._set_plan("investigate", ["probe", "report"])
    bad = AgentDecision(intent="quit", action={"type": "wait"},
                        plan_update={"status": "abandon"})
    agent._tick = 3
    note = agent._apply_plan_update(bad)
    assert note == "abandon_rejected_no_reason"
    assert agent.state.current_plan is not None
    good = AgentDecision(intent="quit", action={"type": "wait"},
                         plan_update={"status": "abandon", "reason": "target_left"})
    note = agent._apply_plan_update(good)
    assert note == "abandoned" and agent.state.current_plan is None


# ---------- full runs ----------
@pytest.fixture(scope="module")
def rule_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    run_dir = asyncio.run(run_simulation(mode="rule", ticks=20, seed=42,
                                         run_name="rule_seed42", out_root=out))
    return run_dir


def test_rule_run_emergent_chain_and_divergent_beliefs(rule_run):
    s = json.loads((rule_run / "summary.json").read_text(encoding="utf-8"))
    assert s["total_events"] > 50
    # objective: only owner (holder) and soldier ever saw the letter
    assert set(s["letter_seen_by_objective"]) <= {"owner", "soldier"}
    soldier = s["final_beliefs"]["soldier"]
    merchant = s["final_beliefs"]["merchant"]
    owner = s["final_beliefs"]["owner"]
    # soldier found and read the letter himself
    assert soldier["anonymous_letter_exists"]["kind"] == "experienced"
    assert soldier["anonymous_letter_exists"]["confidence"] == 1.0
    # merchant never saw it first-hand: at most hearsay
    mb = merchant.get("anonymous_letter_exists")
    assert mb is None or (mb["kind"] != "experienced")
    # the three subjective worlds differ.
    # V0.2-B adjudication (parked item P-1): "owner_hiding_something"
    # and "being_investigated" were products of the old hand-written
    # _interpret table, which the generic interpretation channels
    # replaced. The equivalent inference belief this run actually
    # produces is the OWNER's conceals:merchant — at tick 14 the
    # merchant denies the mayor proposition, contradicting what the
    # owner already believes, so the generic contradiction channel
    # has the owner infer concealment (kind "inferred"). The soldier
    # forms no conceals:* belief in this trajectory: no denial he
    # witnesses contradicts a belief he holds.
    assert owner["conceals:merchant"]["kind"] == "inferred"
    assert owner["conceals:merchant"]["confidence"] == 0.556
    assert not any(k.startswith("conceals:") for k in soldier)
    # "being_investigated" has no generic writer under the new
    # channels; its assertion is deleted (adjudication P-1), not
    # re-pinned to a substitute belief.


def test_information_isolation_memory_subset_of_deliveries(rule_run):
    log = json.loads((rule_run / "world_log.json").read_text(encoding="utf-8"))
    delivered: dict[str, set] = {}
    for rec in log:
        for d in rec["deliveries"]:
            # muffled notices are also legal deliveries (existence, not content)
            delivered.setdefault(d["agent_id"], set()).update(d["delivered_event_ids"])
            delivered[d["agent_id"]].update(d["muffled_event_ids"])
    for cid in ("owner", "merchant", "soldier"):
        state = json.loads((rule_run / "characters" / cid / "state.json")
                           .read_text(encoding="utf-8"))
        perceived = set(state["perceived_event_ids"])
        assert perceived <= delivered[cid], f"{cid} remembers undelivered events"


def test_decision_audit_rows_in_sqlite(rule_run):
    store = Store("rule_seed42", sqlite_path=str(rule_run / "acp.sqlite3"))
    counts = store.counts()
    assert counts["decisions"] == 20 * 3
    assert counts["events"] > 50
    assert counts["deliveries"] == 20 * 3


def test_mock_run_completes(tmp_path):
    run_dir = asyncio.run(run_simulation(mode="mock", ticks=10, seed=7,
                                         run_name="mock_seed7", out_root=tmp_path))
    s = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert s["ticks"] == 10 and s["total_events"] > 0
    assert (run_dir / "narrative.md").exists()
