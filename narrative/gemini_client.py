"""Minimal Gemini client for the narrative layer (Phase 6).

Mirrors llm_client.py's vault discipline for the Gemini
connector: per request, the authd surrogate for a registered
credential is attached host-pinned to
generativelanguage.googleapis.com; the real key never touches
this machine. A small credential pool is supported via
ACP_GEMINI_CREDENTIALS (comma-separated registered credential
names, default "custom.gemini"); a 429 rotates to the next
credential. Model: gemini-2.5-flash (verified for this key).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_SKILL_BIN = "/opt/muse/skills/custom-connector-setup/bin"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MODEL = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-3.8-flash"
_MODEL_FOR: dict[str, str] = {}
_MIN_INTERVAL = 4.0  # free-tier pacing, seconds between calls
_last_call = 0.0


class LLMError(RuntimeError):
    pass


def _credentials() -> list[str]:
    raw = os.environ.get("ACP_GEMINI_CREDENTIALS", "")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if names:
        return names
    pool_file = Path(__file__).resolve().parent.parent / \
        "gemini_pool.txt"
    if pool_file.exists():
        names = [n.strip() for n in
                 pool_file.read_text(encoding="utf-8")
                 .replace("\n", ",").split(",") if n.strip()]
        if names:
            return names
    return ["custom.gemini"]


def _attach(req: urllib.request.Request, credential: str) -> None:
    if _SKILL_BIN not in sys.path:
        sys.path.insert(0, _SKILL_BIN)
    from dynamic_credentials import add_surrogate_to_request
    host = urllib.parse.urlparse(BASE_URL).hostname
    add_surrogate_to_request(req, credential,
                             allowed_hosts=[host] if host else [])


def _pace() -> None:
    global _last_call
    wait = _last_call + _MIN_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def generate(system: str, user: str, max_tokens: int = 4096,
             temperature: float = 0.7, timeout: int = 300,
             json_mode: bool = True,
             thinking_budget: int = 0) -> tuple[str, dict]:
    """One generateContent call. Returns (text, usage dict with
    prompt/candidates/thoughts token counts). Rotates credentials
    on 429. thinking_budget defaults to 0: gemini-2.5-flash is a
    thinking model whose thoughts burn the output budget (a 4k
    cap produced truncated JSON with 1.4k thought tokens), and
    this is a borrowed free-tier pool — fidelity here comes from
    the assembled context, not chain-of-thought.
    """
    payload: dict = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user",
                      "parts": [{"text": user}]}],
        "generationConfig": {"maxOutputTokens": max_tokens,
                             "temperature": temperature,
                             "thinkingConfig":
                             {"thinkingBudget":
                              thinking_budget}}}
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = \
            "application/json"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    attempts = [(c, wait) for c in _credentials()
                for wait in (0, 20, 45)]
    for credential, wait in attempts:
        if wait:
            time.sleep(wait)
        model = _MODEL_FOR.get(credential, MODEL)
        while True:
            _pace()
            req = urllib.request.Request(
                f"{BASE_URL}/models/{model}:generateContent",
                data=body, method="POST",
                headers={"Content-Type": "application/json"})
            _attach(req, credential)
            try:
                with urllib.request.urlopen(req,
                                            timeout=timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8",
                                       errors="replace")[:300]
                last_error = LLMError(
                    f"Gemini HTTP {e.code}: {text}")
                if e.code == 404 and model == MODEL and \
                        "no longer available to new users" in text:
                    # New-account keys are gated off 2.5-flash;
                    # Google directs them to the fallback model.
                    _MODEL_FOR[credential] = FALLBACK_MODEL
                    model = FALLBACK_MODEL
                    continue
                if e.code in (429, 500, 503):
                    data = None
                    break  # rotate / back off and retry
                raise last_error from e
            except urllib.error.URLError as e:
                raise LLMError(
                    f"Gemini request failed: {e.reason}") from e
        if data is None:
            continue
        candidate = (data.get("candidates") or [{}])[0]
        if candidate.get("finishReason") == "MAX_TOKENS":
            raise LLMError("Gemini output truncated "
                           "(finishReason=MAX_TOKENS)")
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata") or {}
        return text, {"prompt_tokens":
                      usage.get("promptTokenCount", 0),
                      "candidates_tokens":
                      usage.get("candidatesTokenCount", 0),
                      "thoughts_tokens":
                      usage.get("thoughtsTokenCount", 0),
                      "credential": credential,
                      "model": model}
    raise last_error or LLMError("all Gemini credentials exhausted")


def list_models(timeout: int = 60) -> list[str]:
    """Harmless reachability proof (connector doc): model ids."""
    req = urllib.request.Request(f"{BASE_URL}/models",
                                 method="GET")
    _attach(req, _credentials()[0])
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [m.get("name", "") for m in data.get("models", [])]


def available() -> bool:
    try:
        if _SKILL_BIN not in sys.path:
            sys.path.insert(0, _SKILL_BIN)
        from dynamic_credentials import dynamic_credential_entry
        dynamic_credential_entry(_credentials()[0])
        return True
    except Exception:
        return False
