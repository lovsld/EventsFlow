"""Local runtime trio tests: raw Gemini key pool (ACP_GEMINI_KEYS),
interruption resume (--resume), and the quota-aware loop (--loop).

Discipline: rule / mock / stub providers only — no real Gemini call is
ever made (the Gemini tests stub _post_generate at the class level),
and every run writes into tmp_path, never the real runs_v02/.

Hard acceptance for resume: in rule mode, an uninterrupted 12-tick
run and a 7-tick run resumed for 5 more ticks must produce
BYTE-IDENTICAL world_log.json (and identical audit-table counts).
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import v02.providers.gemini_provider as gp
import v02.runner as runner_mod
from v02.config import load_all
from v02.providers.base import ProviderError, QuotaExhaustedError
from v02.providers.mock_provider import MockProvider
from v02.protocol import DecisionContext
from v02.runner import run_simulation

ROOT = Path(__file__).resolve().parent.parent
IDS = ["owner", "merchant", "soldier"]

_WAIT_DECISION = {
    "intent": "wait", "target": None,
    "action": {"type": "wait", "target": None, "target_location": None,
               "object": None, "content": None, "claim": None, "topic": None},
    "reasoning_summary": "pool test", "expected_outcome": "observe",
    "confidence": 0.5,
    "plan_update": {"status": "none", "reason": None,
                    "new_goal": None, "new_steps": []}}


def _ctx() -> DecisionContext:
    return DecisionContext(character_id="owner", tick=0, current_time="21:00",
                           location="bar", perception={}, active_goals=[])


def _ok_payload() -> dict:
    return {"candidates": [{"content": {"parts": [
        {"text": json.dumps(_WAIT_DECISION)}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}


def _gemini_env(monkeypatch, keys: str) -> None:
    """Isolate a Gemini test: raw pool in, every other source out."""
    monkeypatch.setenv("ACP_GEMINI_KEYS", keys)
    monkeypatch.setenv("ACP_GEMINI_MIN_INTERVAL", "0")
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ACP_GEMINI_CREDENTIALS",
                "ACP_LLM_PROVIDER", "ACP_MODEL"):
        monkeypatch.delenv(var, raising=False)
    gp._EXHAUSTED_CREDENTIALS.clear()


def _no_sleep(monkeypatch) -> None:
    """Make the provider's one long quota retry instant in tests."""
    real_sleep = asyncio.sleep

    async def fast(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast)


# ================= 件一: raw key pool =================

def test_raw_key_pool_rotates_with_ordinal_labels(monkeypatch):
    _gemini_env(monkeypatch, "SENTINEL-KEY-AAA, SENTINEL-KEY-BBB")
    provider = gp.GeminiProvider(load_all()["owner"], seed=42)
    assert provider.credentials == ["key#1", "key#2"]

    def fake_post(self, payload, credential=None):
        if credential == "key#1":
            raise gp.ProviderError("HTTP 429 from Gemini API: quota")
        return _ok_payload()

    monkeypatch.setattr(gp.GeminiProvider, "_post_generate", fake_post)
    result = asyncio.run(provider.generate_decision(_ctx()))
    assert result.decision.action.type == "wait"
    assert "key#1" in gp._EXHAUSTED_CREDENTIALS
    gp._EXHAUSTED_CREDENTIALS.clear()


def test_raw_key_wins_over_env_key_in_request_header(monkeypatch):
    _gemini_env(monkeypatch, "SENTINEL-RAW-KEY")
    monkeypatch.setenv("GEMINI_API_KEY", "SENTINEL-ENV-KEY")
    provider = gp.GeminiProvider(load_all()["owner"], seed=42)
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"candidates": []}'

    class _Opener:
        def open(self, req, timeout=None):
            captured["headers"] = {k.lower(): v
                                   for k, v in req.header_items()}
            return _Resp()

    monkeypatch.setattr("urllib.request.build_opener",
                        lambda *a, **k: _Opener())
    provider._post_generate({"contents": []}, credential="key#1")
    assert captured["headers"]["x-goog-api-key"] == "SENTINEL-RAW-KEY"


def test_quota_exhausted_is_structured_and_never_leaks_key(monkeypatch):
    _gemini_env(monkeypatch, "SENTINEL-ONLY-KEY-1, SENTINEL-ONLY-KEY-2")
    _no_sleep(monkeypatch)
    provider = gp.GeminiProvider(load_all()["owner"], seed=42)

    def always_429(self, payload, credential=None):
        raise gp.ProviderError("HTTP 429 from Gemini API: quota exceeded")

    monkeypatch.setattr(gp.GeminiProvider, "_post_generate", always_429)
    with pytest.raises(QuotaExhaustedError) as excinfo:
        asyncio.run(provider.generate_decision(_ctx()))
    text = str(excinfo.value)
    assert "SENTINEL-ONLY-KEY-1" not in text
    assert "SENTINEL-ONLY-KEY-2" not in text
    assert "key#1" in gp._EXHAUSTED_CREDENTIALS
    gp._EXHAUSTED_CREDENTIALS.clear()


def test_credential_available_recognizes_raw_pool(monkeypatch):
    _gemini_env(monkeypatch, "SENTINEL-X")
    assert gp.credential_available() is True


def test_llm_run_with_raw_pool_never_persists_sentinel_key(
        monkeypatch, tmp_path):
    """End-to-end leak check: an llm run whose pool always 429s degrades
    to waits; the sentinel key text must appear in NO persisted artefact
    (SQLite file bytes, world_log, summary, checkpoint, state files)."""
    _gemini_env(monkeypatch, "SENTINEL-DB-LEAK-CHECK")
    _no_sleep(monkeypatch)

    def always_429(self, payload, credential=None):
        raise gp.ProviderError("HTTP 429 from Gemini API: quota exceeded")

    monkeypatch.setattr(gp.GeminiProvider, "_post_generate", always_429)
    run_dir = asyncio.run(run_simulation(
        mode="llm", ticks=2, seed=42, run_name="leakcheck", out_root=tmp_path))
    sentinel = b"SENTINEL-DB-LEAK-CHECK"
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert sentinel not in path.read_bytes(), path
    # and the run really did degrade through the quota error path
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["world_tick"] == 2
    con = sqlite3.connect(run_dir / "acp.sqlite3")
    errors = con.execute(
        "SELECT COUNT(*) FROM agent_decisions WHERE error LIKE '%quota%'"
    ).fetchone()[0]
    assert errors == 6  # 3 agents x 2 ticks, all degraded waits
    gp._EXHAUSTED_CREDENTIALS.clear()


# ================= 件二: --resume =================

def _counts(db_path: Path) -> tuple:
    con = sqlite3.connect(db_path)
    return con.execute(
        "SELECT (SELECT COUNT(*) FROM agent_decisions),"
        " (SELECT COUNT(*) FROM world_events),"
        " (SELECT COUNT(*) FROM event_deliveries),"
        " (SELECT COUNT(*) FROM dedup_drops),"
        " (SELECT COUNT(*) FROM predictions),"
        " (SELECT COUNT(*) FROM belief_changes),"
        " (SELECT COUNT(*) FROM plan_transitions),"
        " (SELECT COUNT(*) FROM memory_consolidations)").fetchone()


def test_resume_split_run_byte_identical(tmp_path):
    """THE hard acceptance: 12 uninterrupted == 7 + resume 5, byte for
    byte, with continuous tick numbering and identical audit counts."""
    full = asyncio.run(run_simulation(mode="rule", ticks=12, seed=42,
                                      run_name="full12", out_root=tmp_path))
    part = asyncio.run(run_simulation(mode="rule", ticks=7, seed=42,
                                      run_name="split", out_root=tmp_path))
    resumed = asyncio.run(run_simulation(mode="rule", ticks=5, seed=42,
                                         out_root=tmp_path,
                                         resume_from="split"))
    assert resumed == part
    assert (part / "world_log.json").read_bytes() == \
        (full / "world_log.json").read_bytes()
    assert _counts(part / "acp.sqlite3") == _counts(full / "acp.sqlite3")
    con = sqlite3.connect(part / "acp.sqlite3")
    ticks = [r[0] for r in con.execute(
        "SELECT DISTINCT tick FROM agent_decisions ORDER BY tick")]
    assert ticks == list(range(12))  # continuous, no duplicates


def test_resume_completed_run_byte_identical(tmp_path):
    """Resuming a COMPLETED run replays its final perception pass from
    the checkpoint (rollback) — the extended run still matches the
    uninterrupted one exactly, audit tables included."""
    full = asyncio.run(run_simulation(mode="rule", ticks=15, seed=42,
                                      run_name="full15", out_root=tmp_path))
    done = asyncio.run(run_simulation(mode="rule", ticks=12, seed=42,
                                      run_name="done12", out_root=tmp_path))
    asyncio.run(run_simulation(mode="rule", ticks=3, seed=42,
                               out_root=tmp_path, resume_from="done12"))
    assert (done / "world_log.json").read_bytes() == \
        (full / "world_log.json").read_bytes()
    assert _counts(done / "acp.sqlite3") == _counts(full / "acp.sqlite3")
    for cid in IDS:  # memory.md continues, never duplicates
        assert (done / "characters" / cid / "memory.md").read_bytes() == \
            (full / "characters" / cid / "memory.md").read_bytes()


def test_resume_after_sigkill_byte_identical(tmp_path):
    """A process SIGKILLed mid-run leaves per-tick checkpoints; resuming
    from the last committed tick reproduces the uninterrupted run."""
    full = asyncio.run(run_simulation(mode="rule", ticks=12, seed=42,
                                      run_name="full12", out_root=tmp_path))
    driver = ("import asyncio, sys; from pathlib import Path;"
              "from v02.runner import run_simulation;"
              "asyncio.run(run_simulation(mode='rule', ticks=60, seed=42,"
              " run_name='killed', out_root=Path(sys.argv[1])))")
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    proc = subprocess.Popen([sys.executable, "-c", driver, str(tmp_path)],
                            env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    import time
    cp_path = tmp_path / "killed" / "checkpoint.json"
    killed_at = 0
    deadline = time.time() + 60
    while time.time() < deadline:
        if cp_path.exists():
            try:
                killed_at = json.loads(
                    cp_path.read_text(encoding="utf-8"))["world_tick"]
            except (json.JSONDecodeError, KeyError):
                killed_at = 0
            if killed_at >= 3:
                break
        time.sleep(0.02)
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    killed_at = json.loads(cp_path.read_text(encoding="utf-8"))["world_tick"]
    assert 1 <= killed_at < 12
    asyncio.run(run_simulation(mode="rule", ticks=12 - killed_at, seed=42,
                               out_root=tmp_path, resume_from="killed"))
    assert (tmp_path / "killed" / "world_log.json").read_bytes() == \
        (full / "world_log.json").read_bytes()


def test_resume_mode_mismatch_rejected(tmp_path):
    asyncio.run(run_simulation(mode="rule", ticks=2, seed=42,
                               run_name="m", out_root=tmp_path))
    with pytest.raises(ValueError, match="mode"):
        asyncio.run(run_simulation(mode="mock", ticks=1,
                                   out_root=tmp_path, resume_from="m"))
    with pytest.raises(FileNotFoundError):
        asyncio.run(run_simulation(mode="rule", ticks=1,
                                   out_root=tmp_path, resume_from="nope"))


def test_checkpoint_and_meta_written(tmp_path):
    run_dir = asyncio.run(run_simulation(mode="rule", ticks=3, seed=42,
                                         run_name="meta", out_root=tmp_path))
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "rule" and meta["seed"] == 42
    assert meta["characters"] == IDS and "created_at" in meta
    checkpoint = json.loads(
        (run_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["world_tick"] == 3
    assert len(checkpoint["tick_records"]) == 3
    # world_log was written incrementally with the same final content
    assert len(json.loads(
        (run_dir / "world_log.json").read_text(encoding="utf-8"))) == 3


# ================= 件三: --loop =================

class _QuotaStub(MockProvider):
    """Fails with the structured quota error for the first N calls
    (counted across all agent instances), then behaves like mock."""
    calls = 0
    fail_first = 2

    async def generate_decision(self, context):
        type(self).calls += 1
        if type(self).calls <= type(self).fail_first:
            raise QuotaExhaustedError("stub: whole pool returned 429")
        return await super().generate_decision(context)


class _AlwaysQuota(MockProvider):
    async def generate_decision(self, context):
        raise QuotaExhaustedError("stub: always 429")


class _FlakyOnce(MockProvider):
    """One non-quota failure (HTTP 500), then normal mock behaviour."""
    calls = 0

    async def generate_decision(self, context):
        type(self).calls += 1
        if type(self).calls == 1:
            raise ProviderError("HTTP 500 from Gemini API: boom")
        return await super().generate_decision(context)


def _patch_make_provider(monkeypatch, cls):
    """Swap runner's provider factory so mock mode yields the stub.
    Returns the original factory for explicit restoration."""
    orig = runner_mod.make_provider

    def fake(mode, config, seed=42):
        if mode == "mock":
            return cls(config, seed)
        return orig(mode, config, seed)

    monkeypatch.setattr(runner_mod, "make_provider", fake)
    return orig


def _loop_env(monkeypatch, max_wait_hours: str = "1") -> None:
    monkeypatch.setenv("ACP_QUOTA_SLEEP_MINUTES", "0.001")  # ~60 ms
    monkeypatch.setenv("ACP_QUOTA_MAX_WAIT_HOURS", max_wait_hours)


def test_loop_quota_pause_retries_same_tick(monkeypatch, tmp_path):
    _loop_env(monkeypatch)
    _QuotaStub.calls = 0
    orig = _patch_make_provider(monkeypatch, _QuotaStub)
    loop_dir = asyncio.run(run_simulation(
        mode="mock", ticks=6, seed=42, run_name="looprun",
        out_root=tmp_path, loop=True))
    monkeypatch.setattr(runner_mod, "make_provider", orig)
    base_dir = asyncio.run(run_simulation(
        mode="mock", ticks=6, seed=42, run_name="baserun", out_root=tmp_path))
    # the paused tick was retried, not burned: trajectory is the
    # no-fault trajectory, byte for byte
    assert (loop_dir / "world_log.json").read_bytes() == \
        (base_dir / "world_log.json").read_bytes()
    summary = json.loads((loop_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["quota_stopped"] is False
    assert summary["quota_waits"] >= 1
    assert summary["world_tick"] == 6
    con = sqlite3.connect(loop_dir / "acp.sqlite3")
    per_tick = con.execute(
        "SELECT tick, COUNT(*) FROM agent_decisions GROUP BY tick"
        " ORDER BY tick").fetchall()
    assert per_tick == [(t, 3) for t in range(6)]  # no degraded dupes
    errors = con.execute(
        "SELECT COUNT(*) FROM agent_decisions WHERE error IS NOT NULL"
    ).fetchone()[0]
    assert errors == 0  # failed attempts left no audit rows


def test_loop_budget_exhausted_stops_cleanly_and_resumes(
        monkeypatch, tmp_path):
    _loop_env(monkeypatch, max_wait_hours="0")  # no waiting allowed
    orig = _patch_make_provider(monkeypatch, _AlwaysQuota)
    stop_dir = asyncio.run(run_simulation(
        mode="mock", ticks=5, seed=42, run_name="stoprun",
        out_root=tmp_path, loop=True))
    summary = json.loads((stop_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["quota_stopped"] is True
    assert summary["world_tick"] == 0  # the blocked tick never committed
    assert json.loads((stop_dir / "world_log.json").read_text(
        encoding="utf-8")) == []
    con = sqlite3.connect(stop_dir / "acp.sqlite3")
    assert con.execute("SELECT COUNT(*) FROM agent_decisions").fetchone()[0] == 0
    assert json.loads((stop_dir / "checkpoint.json").read_text(
        encoding="utf-8"))["world_tick"] == 0
    # the loop's checkpoint is the resume checkpoint: a later --resume
    # (plain mock now) completes the run on the no-fault trajectory
    monkeypatch.setattr(runner_mod, "make_provider", orig)
    asyncio.run(run_simulation(mode="mock", ticks=5, seed=42,
                               out_root=tmp_path, resume_from="stoprun"))
    base_dir = asyncio.run(run_simulation(
        mode="mock", ticks=5, seed=42, run_name="base5", out_root=tmp_path))
    assert (stop_dir / "world_log.json").read_bytes() == \
        (base_dir / "world_log.json").read_bytes()


def test_loop_non_quota_error_still_degrades(monkeypatch, tmp_path):
    _loop_env(monkeypatch)
    _FlakyOnce.calls = 0
    _patch_make_provider(monkeypatch, _FlakyOnce)
    run_dir = asyncio.run(run_simulation(
        mode="mock", ticks=3, seed=42, run_name="flaky",
        out_root=tmp_path, loop=True))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["quota_stopped"] is False
    assert summary["quota_waits"] == 0  # a 500 never triggers a pause
    assert summary["world_tick"] == 3
    con = sqlite3.connect(run_dir / "acp.sqlite3")
    errors = con.execute(
        "SELECT tick, error FROM agent_decisions WHERE error IS NOT NULL"
    ).fetchall()
    assert len(errors) == 1 and errors[0][0] == 0 and "500" in errors[0][1]
