"""Minimal DeepSeek client for the narrative layer (design §6.5).

Independent of v02 (no imports): the credential path mirrors the
emergence layer's vault discipline — the raw key never touches this
machine. Per request, the authd surrogate for the Muse connector
credential "custom.deepseek" is fetched fresh and attached to the
request, host-pinned to api.deepseek.com; the egress layer swaps in
the real key for that host only. Nothing credential-shaped is stored,
logged, or written to any product.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

_SKILL_BIN = "/opt/muse/skills/custom-connector-setup/bin"
CREDENTIAL_NAME = "custom.deepseek"
BASE_URL = "https://api.deepseek.com/v1"
MODEL = "deepseek-flash"  # the account's actual model name (V0.2-C §7)


class LLMError(RuntimeError):
    pass


def _attach_surrogate(req: urllib.request.Request) -> None:
    if _SKILL_BIN not in sys.path:
        sys.path.insert(0, _SKILL_BIN)
    from dynamic_credentials import add_surrogate_to_request
    host = urllib.parse.urlparse(BASE_URL).hostname
    add_surrogate_to_request(req, CREDENTIAL_NAME,
                             allowed_hosts=[host] if host else [])


def chat(messages: list[dict], max_tokens: int = 8192,
         temperature: float = 0.7, timeout: int = 300) -> tuple[str, dict]:
    """One chat completion. Returns (content, usage dict)."""
    payload = {"model": MODEL, "messages": messages,
               "max_tokens": max_tokens, "temperature": temperature,
               "response_format": {"type": "json_object"}}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    _attach_surrogate(req)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")[:400]
        raise LLMError(f"DeepSeek HTTP {e.code}: {text}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"DeepSeek request failed: {e.reason}") from e
    content = data["choices"][0]["message"]["content"]
    return content, data.get("usage") or {}


def available() -> bool:
    """Reachability of the vault credential (value discarded)."""
    try:
        if _SKILL_BIN not in sys.path:
            sys.path.insert(0, _SKILL_BIN)
        from dynamic_credentials import dynamic_credential_entry
        dynamic_credential_entry(CREDENTIAL_NAME)
        return True
    except Exception:
        return False
