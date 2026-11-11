"""Multi-backend tests: the OpenAI-compatible provider pool serving
DeepSeek / Kimi / Qwen / OpenAI behind ACP_PROVIDER / --provider.

Discipline: stub transport only — _post_chat is replaced at the class
level, or urllib's opener is stubbed to capture the request — so NO
real API is ever called and no money is spent. Every run writes into
tmp_path, never the real runs_v02/.

Acceptance covered: per-preset request shape (base_url path, Bearer
header with the current key, default model, json_object format), pool
rotation on 429/401 with ordinal key labels, structured
QuotaExhaustedError on whole-pool 429, single-key env fallback,
per-backend preflight naming the missing env vars, a DeepSeek
end-to-end 3-tick run, --loop quota pause on the new backend, resume
backend pinning, and a sentinel-key leak scan over every run artefact.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

import v02.providers.openai_provider as cp
from v02.config import load_all
from v02.providers import make_provider
from v02.providers.base import ProviderError, QuotaExhaustedError
from v02.protocol import DecisionContext
from v02.runner import preflight, run_simulation

ROOT = Path(__file__).resolve().parent.parent

_WAIT_DECISION = {
    "intent": "wait", "target": None,
    "action": {"type": "wait", "target": None, "target_location": None,
               "object": None, "content": None, "claim": None, "topic": None},
    "reasoning_summary": "backend test", "expected_outcome": "observe",
    "confidence": 0.5,
    "plan_update": {"status": "none", "reason": None,
                    "new_goal": None, "new_steps": []}}

_ALL_BACKEND_ENVS = (
    "ACP_PROVIDER", "ACP_LLM_PROVIDER", "ACP_MODEL", "ACP_MODEL_OWNER",
    "ACP_MODEL_MERCHANT", "ACP_MODEL_SOLDIER", "ACP_TEMPERATURE",
    "ACP_GEMINI_KEYS", "ACP_GEMINI_CREDENTIALS", "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "ACP_DEEPSEEK_KEYS", "DEEPSEEK_API_KEY", "ACP_DEEPSEEK_BASE_URL",
    "ACP_DEEPSEEK_MIN_INTERVAL",
    "ACP_KIMI_KEYS", "MOONSHOT_API_KEY", "ACP_KIMI_BASE_URL",
    "ACP_KIMI_MIN_INTERVAL",
    "ACP_QWEN_KEYS", "DASHSCOPE_API_KEY", "ACP_QWEN_BASE_URL",
    "ACP_QWEN_MIN_INTERVAL",
    "ACP_OPENAI_KEYS", "OPENAI_API_KEY", "ACP_OPENAI_BASE_URL",
    "ACP_OPENAI_MIN_INTERVAL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ALL_BACKEND_ENVS:
        monkeypatch.delenv(var, raising=False)
    cp._EXHAUSTED_KEYS.clear()
    yield
    cp._EXHAUSTED_KEYS.clear()


def _ctx() -> DecisionContext:
    return DecisionContext(character_id="owner", tick=0, current_time="21:00",
                           location="bar", perception={}, active_goals=[])


def _chat_payload() -> dict:
    return {"choices": [{"message": {"content": json.dumps(_WAIT_DECISION)}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7}}


def _stub_post(monkeypatch, fake) -> None:
    monkeypatch.setattr(cp.OpenAICompatibleProvider, "_post_chat", fake)


def _ok_post(self, payload, credential=None):
    return _chat_payload()


class _CaptureOpener:
    """urllib opener stub: captures the request, replies with a canned
    chat-completions payload. No network is touched."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.captured: dict = {}

    def open(self, req, timeout=None):
        self.captured = {
            "url": req.full_url,
            "headers": {k.lower(): v for k, v in req.header_items()},
            "body": json.loads(req.data.decode("utf-8")),
        }
        payload = self.payload

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        return _Resp()


# ================= presets & request shape =================

_PRESET_CASES = [
    ("deepseek", "ACP_DEEPSEEK_KEYS", "https://api.deepseek.com/v1",
     "deepseek-chat"),
    ("kimi", "ACP_KIMI_KEYS", "https://api.moonshot.cn/v1",
     "moonshot-v1-8k"),
    ("qwen", "ACP_QWEN_KEYS",
     "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-turbo"),
    ("openai", "ACP_OPENAI_KEYS", "https://api.openai.com/v1",
     "gpt-4o-mini"),
]


@pytest.mark.parametrize("backend,keys_env,base_url,model", _PRESET_CASES)
def test_preset_request_shape(monkeypatch, backend, keys_env, base_url,
                              model):
    monkeypatch.setenv(keys_env, "SENTINEL-SHAPE-KEY")
    provider = make_provider("llm", load_all()["owner"], provider=backend)
    assert provider.name == backend and provider.model == model
    assert provider.credentials == ["key#1"]
    opener = _CaptureOpener(_chat_payload())
    monkeypatch.setattr("urllib.request.build_opener",
                        lambda *a, **k: opener)
    result = asyncio.run(provider.generate_decision(_ctx()))
    assert opener.captured["url"] == f"{base_url}/chat/completions"
    assert opener.captured["headers"]["authorization"] == \
        "Bearer SENTINEL-SHAPE-KEY"
    body = opener.captured["body"]
    assert body["model"] == model
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert result.decision.action.type == "wait"
    assert result.provider == backend and result.model == model
    assert result.input_tokens == 11 and result.output_tokens == 7


def test_base_url_env_override(monkeypatch):
    monkeypatch.setenv("ACP_QWEN_KEYS", "SENTINEL-QWEN-KEY")
    monkeypatch.setenv("ACP_QWEN_BASE_URL", "http://127.0.0.1:9/proxy/v1/")
    provider = make_provider("llm", load_all()["owner"], provider="qwen")
    assert provider.base_url == "http://127.0.0.1:9/proxy/v1"
    opener = _CaptureOpener(_chat_payload())
    monkeypatch.setattr("urllib.request.build_opener",
                        lambda *a, **k: opener)
    asyncio.run(provider.generate_decision(_ctx()))
    assert opener.captured["url"] == \
        "http://127.0.0.1:9/proxy/v1/chat/completions"


# ================= key pool semantics =================

def test_pool_rotates_on_429_with_ordinal_labels(monkeypatch):
    monkeypatch.setenv("ACP_DEEPSEEK_KEYS", "SENTINEL-KEY-A, SENTINEL-KEY-B")
    provider = make_provider("llm", load_all()["owner"], provider="deepseek")
    assert provider.credentials == ["key#1", "key#2"]
    seen = []

    def fake(self, payload, credential=None):
        seen.append(credential)
        if credential == "key#1":
            raise ProviderError("HTTP 429 from deepseek API: rate limited")
        return _chat_payload()

    _stub_post(monkeypatch, fake)
    result = asyncio.run(provider.generate_decision(_ctx()))
    assert result.decision.action.type == "wait"
    assert seen == ["key#1", "key#2"]
    assert ("deepseek", "key#1") in cp._EXHAUSTED_KEYS


def test_pool_all_429_raises_quota_exhausted_without_leaking(monkeypatch):
    monkeypatch.setenv("ACP_DEEPSEEK_KEYS",
                       "SENTINEL-QUOTA-A, SENTINEL-QUOTA-B")

    def always_429(self, payload, credential=None):
        raise ProviderError("HTTP 429 from deepseek API: quota exceeded")

    _stub_post(monkeypatch, always_429)
    provider = make_provider("llm", load_all()["owner"], provider="deepseek")
    with pytest.raises(QuotaExhaustedError) as excinfo:
        asyncio.run(provider.generate_decision(_ctx()))
    text = str(excinfo.value)
    assert "SENTINEL-QUOTA-A" not in text and "SENTINEL-QUOTA-B" not in text
    assert ("deepseek", "key#1") in cp._EXHAUSTED_KEYS
    assert ("deepseek", "key#2") in cp._EXHAUSTED_KEYS


def test_pool_rotates_on_401(monkeypatch):
    monkeypatch.setenv("ACP_KIMI_KEYS", "SENTINEL-KIMI-A, SENTINEL-KIMI-B")

    def fake(self, payload, credential=None):
        if credential == "key#1":
            raise ProviderError("HTTP 401 from kimi API: invalid key")
        return _chat_payload()

    _stub_post(monkeypatch, fake)
    provider = make_provider("llm", load_all()["owner"], provider="kimi")
    result = asyncio.run(provider.generate_decision(_ctx()))
    assert result.decision.action.type == "wait"
    assert ("kimi", "key#1") in cp._EXHAUSTED_KEYS


def test_pool_all_401_is_plain_provider_error(monkeypatch):
    monkeypatch.setenv("ACP_KIMI_KEYS", "SENTINEL-KIMI-A, SENTINEL-KIMI-B")

    def always_401(self, payload, credential=None):
        raise ProviderError("HTTP 401 from kimi API: invalid key")

    _stub_post(monkeypatch, always_401)
    provider = make_provider("llm", load_all()["owner"], provider="kimi")
    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(provider.generate_decision(_ctx()))
    assert not isinstance(excinfo.value, QuotaExhaustedError)


def test_single_key_env_fallback(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "SENTINEL-SINGLE")
    provider = make_provider("llm", load_all()["owner"], provider="deepseek")
    assert provider.credentials == ["key#1"]
    _stub_post(monkeypatch, _ok_post)
    result = asyncio.run(provider.generate_decision(_ctx()))
    assert result.decision.action.type == "wait"


def test_missing_key_error_names_env_vars(monkeypatch):
    provider = make_provider("llm", load_all()["owner"], provider="deepseek")
    assert provider.credentials == []
    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(provider.generate_decision(_ctx()))
    assert "ACP_DEEPSEEK_KEYS" in str(excinfo.value)
    assert "DEEPSEEK_API_KEY" in str(excinfo.value)


def test_keys_available_and_pacer_interval(monkeypatch):
    assert cp.keys_available("qwen") is False
    monkeypatch.setenv("DASHSCOPE_API_KEY", "SENTINEL-X")
    assert cp.keys_available("qwen") is True
    assert cp.credential_available() is False  # openai preset untouched
    monkeypatch.setenv("ACP_QWEN_MIN_INTERVAL", "3")
    assert cp._interval_for("qwen") == 3.0
    assert cp._interval_for("deepseek") == 0.0


# ================= backend selection =================

def test_backend_selection_via_env_and_argument(monkeypatch):
    cfg = load_all()["owner"]
    monkeypatch.setenv("ACP_PROVIDER", "kimi")
    kimi = make_provider("llm", cfg, seed=42)
    assert isinstance(kimi, cp.KimiProvider) and kimi.model == "moonshot-v1-8k"
    qwen = make_provider("llm", cfg, seed=42, provider="qwen")
    assert isinstance(qwen, cp.QwenProvider) and qwen.model == "qwen-turbo"
    # explicit argument beats the env var
    ds = make_provider("llm", cfg, seed=42, provider="deepseek")
    assert isinstance(ds, cp.DeepSeekProvider)


def test_model_overrides_apply_to_new_backends(monkeypatch):
    cfgs = load_all()
    monkeypatch.setenv("ACP_MODEL", "deepseek-reasoner")
    p = make_provider("llm", cfgs["owner"], seed=42, provider="deepseek")
    assert p.model == "deepseek-reasoner"
    monkeypatch.delenv("ACP_MODEL")
    monkeypatch.setenv("ACP_MODEL_SOLDIER", "qwen-plus")
    soldier = make_provider("llm", cfgs["soldier"], seed=42, provider="qwen")
    assert soldier.model == "qwen-plus"
    owner = make_provider("llm", cfgs["owner"], seed=42, provider="qwen")
    assert owner.model == "qwen-turbo"


def test_preflight_names_backend_env_vars(monkeypatch):
    monkeypatch.setenv("ACP_PROVIDER", "deepseek")
    with pytest.raises(RuntimeError) as excinfo:
        preflight("llm")
    assert "ACP_DEEPSEEK_KEYS" in str(excinfo.value)
    assert "DEEPSEEK_API_KEY" in str(excinfo.value)
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        preflight("llm", provider="qwen")
    monkeypatch.setenv("ACP_KIMI_KEYS", "SENTINEL-K")
    preflight("llm", provider="kimi")  # passes with its own pool set
    preflight("rule")
    preflight("mock")


# ================= end-to-end (stub transport) =================

def test_deepseek_end_to_end_three_ticks(monkeypatch, tmp_path):
    monkeypatch.setenv("ACP_DEEPSEEK_KEYS", "SENTINEL-E2E-KEY")
    _stub_post(monkeypatch, _ok_post)
    run_dir = asyncio.run(run_simulation(
        mode="llm", ticks=3, seed=42, run_name="ds_e2e",
        out_root=tmp_path, provider="deepseek"))
    world_log = json.loads((run_dir / "world_log.json").read_text(
        encoding="utf-8"))
    assert len(world_log) == 3
    summary = json.loads((run_dir / "summary.json").read_text(
        encoding="utf-8"))
    assert summary["llm_backend"] == "deepseek"
    assert summary["world_tick"] == 3
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["backend"] == "deepseek" and meta["mode"] == "llm"
    con = sqlite3.connect(run_dir / "acp.sqlite3")
    total, errors = con.execute(
        "SELECT COUNT(*), SUM(error IS NOT NULL) FROM agent_decisions"
    ).fetchone()
    assert total == 9 and errors == 0  # 3 agents x 3 ticks, all decided
    providers = {r[0] for r in con.execute(
        "SELECT DISTINCT provider FROM agent_decisions")}
    assert providers == {"deepseek"}
    sentinel = b"SENTINEL-E2E-KEY"
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert sentinel not in path.read_bytes(), path


def test_deepseek_sentinel_key_never_persisted_on_quota_degrade(
        monkeypatch, tmp_path):
    monkeypatch.setenv("ACP_DEEPSEEK_KEYS", "SENTINEL-DS-LEAK-CHECK")

    def always_429(self, payload, credential=None):
        raise ProviderError("HTTP 429 from deepseek API: quota exceeded")

    _stub_post(monkeypatch, always_429)
    run_dir = asyncio.run(run_simulation(
        mode="llm", ticks=2, seed=42, run_name="ds_leak",
        out_root=tmp_path, provider="deepseek"))
    summary = json.loads((run_dir / "summary.json").read_text(
        encoding="utf-8"))
    assert summary["world_tick"] == 2  # degraded waits, world still runs
    sentinel = b"SENTINEL-DS-LEAK-CHECK"
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert sentinel not in path.read_bytes(), path
    con = sqlite3.connect(run_dir / "acp.sqlite3")
    errors = con.execute(
        "SELECT COUNT(*) FROM agent_decisions WHERE error LIKE '%quota%'"
    ).fetchone()[0]
    assert errors == 6  # 3 agents x 2 ticks, all quota-degraded


# ================= --loop on a new backend =================

def test_loop_deepseek_quota_pause_never_burns_tick(monkeypatch, tmp_path):
    monkeypatch.setenv("ACP_DEEPSEEK_KEYS", "SENTINEL-LOOP-KEY")
    monkeypatch.setenv("ACP_QUOTA_SLEEP_MINUTES", "0.001")
    monkeypatch.setenv("ACP_QUOTA_MAX_WAIT_HOURS", "1")
    state = {"calls": 0}

    def flaky(self, payload, credential=None):
        state["calls"] += 1
        if state["calls"] <= 2:  # pool-wide 429 outage, then recovery
            raise ProviderError("HTTP 429 from deepseek API: quota")
        return _chat_payload()

    _stub_post(monkeypatch, flaky)
    loop_dir = asyncio.run(run_simulation(
        mode="llm", ticks=3, seed=42, run_name="ds_loop",
        out_root=tmp_path, provider="deepseek", loop=True))
    _stub_post(monkeypatch, _ok_post)
    base_dir = asyncio.run(run_simulation(
        mode="llm", ticks=3, seed=42, run_name="ds_clean",
        out_root=tmp_path, provider="deepseek"))
    assert (loop_dir / "world_log.json").read_bytes() == \
        (base_dir / "world_log.json").read_bytes()
    summary = json.loads((loop_dir / "summary.json").read_text(
        encoding="utf-8"))
    assert summary["quota_stopped"] is False
    assert summary["quota_waits"] >= 1
    assert summary["world_tick"] == 3
    con = sqlite3.connect(loop_dir / "acp.sqlite3")
    per_tick = con.execute(
        "SELECT tick, COUNT(*) FROM agent_decisions GROUP BY tick"
        " ORDER BY tick").fetchall()
    assert per_tick == [(t, 3) for t in range(3)]
    errors = con.execute(
        "SELECT COUNT(*) FROM agent_decisions WHERE error IS NOT NULL"
    ).fetchone()[0]
    assert errors == 0  # the paused attempts left no audit rows


# ================= resume pins the backend =================

def test_resume_backend_mismatch_rejected_and_matching_ok(
        monkeypatch, tmp_path):
    monkeypatch.setenv("ACP_DEEPSEEK_KEYS", "SENTINEL-RESUME-KEY")
    _stub_post(monkeypatch, _ok_post)
    run_dir = asyncio.run(run_simulation(
        mode="llm", ticks=2, seed=42, run_name="ds_resume",
        out_root=tmp_path, provider="deepseek"))
    with pytest.raises(ValueError, match="backend"):
        asyncio.run(run_simulation(
            mode="llm", ticks=1, out_root=tmp_path,
            resume_from="ds_resume", provider="kimi"))
    # no provider requested -> the run's own backend (deepseek) is used
    asyncio.run(run_simulation(
        mode="llm", ticks=1, out_root=tmp_path, resume_from="ds_resume"))
    world_log = json.loads((run_dir / "world_log.json").read_text(
        encoding="utf-8"))
    assert len(world_log) == 3
    con = sqlite3.connect(run_dir / "acp.sqlite3")
    providers = {r[0] for r in con.execute(
        "SELECT DISTINCT provider FROM agent_decisions")}
    assert providers == {"deepseek"}
