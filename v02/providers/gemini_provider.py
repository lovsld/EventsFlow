"""Gemini Provider — real LLM decisions via Google AI Studio (Gemini API).

Chosen by Hu on 2026-11-06 as the project's LLM (replacing OpenAI for the
llm mode; the OpenAI provider remains available behind the same interface).

Credential handling: the API key lives in the Muse secure connector
(custom.gemini). This module never sees the real key — it attaches a
surrogate (hsurr:*) via dynamic_credentials, and the egress layer swaps in
the real key for requests to generativelanguage.googleapis.com only.
A directly-set GEMINI_API_KEY environment variable is also accepted for
running outside Muse.

Structured output: Gemini JSON mode (responseMimeType=application/json)
plus Pydantic validation of AgentDecision, one validation-repair retry,
bounded API retries, timeout, token usage from usageMetadata.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

from ..protocol import AgentDecision, DecisionContext, ProviderResult
from .base import LLMProvider, ProviderError, QuotaExhaustedError
from .openai_provider import SYSTEM_TEMPLATE

API_HOST = "generativelanguage.googleapis.com"
_CREDENTIAL_NAME = "custom.gemini"
_SKILL_BIN = "/opt/muse/skills/custom-connector-setup/bin"

# Free-tier throttling is per model and load-dependent (observed
# 2026-11-10: flash-lite took 18 paced calls/minutes cleanly in the smoke
# run, then 429'd under the full run's combined load while standalone
# probes still succeeded). The pacer is therefore keyed by model: each
# model gets its own minimum interval, overridable per model via
# ACP_GEMINI_MIN_INTERVAL_<MODELKEY> (e.g. ..._GEMINI_2_5_FLASH_LITE=40),
# falling back to ACP_GEMINI_MIN_INTERVAL (default 7s).
_PACE_LOCK = asyncio.Lock()
_PACE_LAST: dict[str, float] = {}

# Credential pool: ACP_GEMINI_CREDENTIALS may name several registered
# custom.* credentials (comma-separated). A credential that answers 429
# is marked exhausted for the rest of the process and decisions rotate
# to the next one — this lets a long run draw on several keys' free-tier
# budgets (pool added 2026-11-10, when Hu borrowed extra keys).
_EXHAUSTED_CREDENTIALS: set[str] = set()


def _interval_for(model: str) -> float:
    key = "ACP_GEMINI_MIN_INTERVAL_" + model.upper().replace("-", "_").replace(".", "_")
    return float(os.environ.get(key, os.environ.get("ACP_GEMINI_MIN_INTERVAL", "7")))


async def _pace(model: str = "") -> None:
    interval = _interval_for(model)
    async with _PACE_LOCK:
        now = time.monotonic()
        wait = _PACE_LAST.get(model, 0.0) + interval - now
        if wait > 0:
            await asyncio.sleep(wait)
        _PACE_LAST[model] = time.monotonic()


def _parse_raw_keys() -> list[str]:
    """ACP_GEMINI_KEYS: comma-separated RAW Gemini API keys for running
    fully locally (see LOCAL_DEPLOY.md). The keys themselves are held
    only in the provider instance's private map; everywhere else —
    rotation bookkeeping, logs, error text, the audit DB — a key is
    referred to strictly by its ordinal label ("key#1", "key#2", ...).
    Raw key text must never be printed or persisted."""
    return [k.strip() for k in os.environ.get("ACP_GEMINI_KEYS", "").split(",")
            if k.strip()]


def credential_available() -> bool:
    """True if a Gemini credential is reachable (env var or connector)."""
    if _parse_raw_keys():
        return True
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return True
    try:
        sys.path.insert(0, _SKILL_BIN)
        from dynamic_credentials import dynamic_credential_entry
        dynamic_credential_entry(_CREDENTIAL_NAME)
        return True
    except Exception:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused")


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, config, seed: int = 42, model: str | None = None,
                 temperature: float | None = None, timeout: float = 60.0,
                 max_retries: int = 3):
        super().__init__(config, seed)
        # Default is flash-lite. Free-tier throttling is per model and
        # load-dependent: the API's 429 body cites a limit of 20 on the
        # free-tier requests metric (2026-11-06), yet on 2026-11-10
        # flash-lite served 21+ calls in a day while 429ing under a
        # 3-agent run's combined load. Long runs should spread agents
        # across models (ACP_MODEL_<CHARACTER>) and pace flash-lite
        # widely; see _pace.
        self.model = model or os.environ.get("ACP_MODEL", "gemini-2.5-flash-lite")
        self.temperature = float(os.environ.get("ACP_TEMPERATURE", temperature
                                                if temperature is not None else 0.7))
        self.timeout = timeout
        self.max_retries = max_retries
        # Credential source priority (LOCAL_DEPLOY.md):
        #   1. ACP_GEMINI_KEYS        — raw local key pool; entries are
        #      labelled key#1, key#2, ... and the raw text lives only in
        #      self._raw_keys (never logged, never persisted);
        #   2. ACP_GEMINI_CREDENTIALS — Muse connector credential names
        #      (behaviour unchanged from before);
        #   3. GEMINI_API_KEY single key (handled in _post_generate);
        #   4. the connector default credential.
        self._raw_keys: dict[str, str] = {}
        raw_keys = _parse_raw_keys()
        if raw_keys:
            self.credentials = []
            for i, key in enumerate(raw_keys):
                label = f"key#{i + 1}"
                self.credentials.append(label)
                self._raw_keys[label] = key
        else:
            pool = os.environ.get("ACP_GEMINI_CREDENTIALS", "")
            self.credentials = [c.strip() for c in pool.split(",") if c.strip()] \
                or [_CREDENTIAL_NAME]

    # ---------- transport (sync; run in a thread by the async wrapper) ----------
    def _post_generate(self, payload: dict, credential: str | None = None) -> dict:
        url = f"https://{API_HOST}/v1beta/models/{self.model}:generateContent"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        label = credential or (self.credentials[0] if self.credentials
                               else _CREDENTIAL_NAME)
        env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if label in self._raw_keys:
            # Raw local pool wins over every other source; the key text
            # goes only into this request's header, nowhere else.
            req.add_header("x-goog-api-key", self._raw_keys[label])
        elif env_key:
            req.add_header("x-goog-api-key", env_key)
        else:
            sys.path.insert(0, _SKILL_BIN)
            from dynamic_credentials import add_surrogate_to_request
            add_surrogate_to_request(req, label, allowed_hosts=[API_HOST])
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:400]
            raise ProviderError(f"HTTP {e.code} from Gemini API: {body}") from e

    def _system_prompt(self) -> str:
        cfg = self.config
        return SYSTEM_TEMPLATE.format(
            identity=cfg.identity, personality=json.dumps(cfg.personality, ensure_ascii=False),
            principles="；".join(cfg.principles),
            goals="；".join(g["desc"] for g in cfg.goals)) + \
            "\n输出必须是且仅是一个 JSON 对象，字段为 intent, target, action{type,target," \
            "target_location,object,content,claim,topic}, reasoning_summary, " \
            "expected_outcome, confidence, plan_update{status,reason,new_goal,new_steps}。" \
            "\n枚举值必须严格照抄：action.type 只能是 speak/ask/confront/move/search/show/" \
            "refuse/observe/listen/work/wait 之一；plan_update.status 只能是 " \
            "none/continue/advance/abandon/replace 之一（没有计划变化时用 none）。"

    @staticmethod
    def _sanitize(raw: str) -> dict:
        """Coerce frequent model improvisations into the schema's enums
        before validation. Anything still invalid after this fails loudly
        and is retried — it is never executed."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[start:end + 1])
        action = data.get("action") or {}
        type_map = {"talk": "speak", "say": "speak", "go": "move", "move_to": "move",
                    "question": "ask", "challenge": "confront", "look": "observe",
                    "search_drawer": "search", "do_nothing": "wait"}
        if action.get("type") in type_map:
            action["type"] = type_map[action["type"]]
        pu = data.get("plan_update") or {}
        status_map = {"new_plan": "replace", "create": "replace", "start": "replace",
                      "update": "continue", "keep": "continue", "ongoing": "continue"}
        if pu.get("status") in status_map:
            pu["status"] = status_map[pu["status"]]
        if pu.get("status") not in ("none", "continue", "advance", "abandon", "replace"):
            pu["status"] = "none"
        data["plan_update"] = pu
        conf = data.get("confidence")
        if isinstance(conf, (int, float)):
            data["confidence"] = min(1.0, max(0.0, float(conf)))
        return data

    async def generate_decision(self, context: DecisionContext) -> ProviderResult:
        if not credential_available():
            raise ProviderError(
                "No Gemini credential available (connector custom.gemini not "
                "connected and GEMINI_API_KEY unset); llm mode cannot run.")
        user_text = context.model_dump_json()
        last_error: Exception | None = None
        started = time.monotonic()
        repair_note = ""
        rate_limit_retries = 0
        active = [c for c in self.credentials if c not in _EXHAUSTED_CREDENTIALS]
        cred_idx = 0
        for attempt in range(self.max_retries + 1):
            payload = {
                "systemInstruction": {"parts": [{"text": self._system_prompt()}]},
                "contents": [{"role": "user",
                              "parts": [{"text": user_text + repair_note}]}],
                "generationConfig": {"temperature": self.temperature,
                                     "responseMimeType": "application/json"},
            }
            try:
                await _pace(self.model)
                data = await asyncio.to_thread(
                    self._post_generate, payload,
                    active[cred_idx] if active else None)
                parts = data["candidates"][0]["content"]["parts"]
                raw = "".join(p.get("text", "") for p in parts)
                decision = AgentDecision.model_validate(self._sanitize(raw))
                usage = data.get("usageMetadata", {})
                return ProviderResult(
                    decision=decision, provider=self.name, model=self.model,
                    input_tokens=usage.get("promptTokenCount", 0) or 0,
                    output_tokens=usage.get("candidatesTokenCount", 0) or 0,
                    raw_output=raw, latency_ms=int((time.monotonic() - started) * 1000))
            except Exception as e:
                last_error = e
                if any(code in str(e) for code in ("429", "403", "404")):
                    # Rotate to the next pooled credential first: a fresh
                    # key carries a fresh free-tier budget, and a 404 may
                    # mean this key's account cannot serve this model at
                    # all (observed 2026-11-10: newer Google accounts get
                    # "model no longer available to new users" for the
                    # gemini-2.5 family, while older keys serve it fine).
                    if cred_idx + 1 < len(active):
                        _EXHAUSTED_CREDENTIALS.add(active[cred_idx])
                        cred_idx += 1
                        continue
                if "429" in str(e):
                    # Quota exhaustion on the last credential: at most ONE
                    # long retry, then fail — hammering a cap burns time.
                    # The failure is raised as the structured
                    # QuotaExhaustedError so the scheduler's --loop mode
                    # can pause on the TYPE instead of guessing from text.
                    if rate_limit_retries >= 1 or attempt >= self.max_retries:
                        raise QuotaExhaustedError(
                            f"Gemini quota exhausted (429): {e}") from e
                    rate_limit_retries += 1
                    await asyncio.sleep(40)
                    continue
                if attempt < self.max_retries:
                    repair_note = (f"\n\n注意：上一次输出未通过校验（{type(e).__name__}: {e}）。"
                                   f"请只返回符合要求的 JSON 对象。")
                    await asyncio.sleep(min(2 ** attempt, 4))
        raise ProviderError(f"Gemini decision failed after {self.max_retries + 1} attempts: "
                            f"{type(last_error).__name__}: {last_error}")
