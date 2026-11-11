"""OpenAI-compatible Provider backends — OpenAI / DeepSeek / Kimi / Qwen.

All four vendors expose the same chat-completions shape, so one
parameterized backend (this module) serves all of them; each vendor is
just a preset (base_url / default model / key env vars). Gemini keeps
its own provider — its API shape is different — and remains the default
llm backend; select one of these with ACP_PROVIDER / --provider
(deepseek | kimi | qwen | openai).

Key handling mirrors the Gemini raw pool (LOCAL_DEPLOY.md):
- ACP_<BACKEND>_KEYS: comma-separated RAW keys (the local pool);
  without a pool, the vendor's conventional single-key env var
  (DEEPSEEK_API_KEY / MOONSHOT_API_KEY / DASHSCOPE_API_KEY /
  OPENAI_API_KEY) is used.
- Keys are referred to everywhere — rotation bookkeeping, logs, error
  text, the audit DB — strictly by ordinal label ("key#1", "key#2",
  ...). Raw key text lives only in the provider instance's private map
  and in the Authorization header of the request itself.
- A key answering 429/401/403 is marked exhausted for the process and
  the next key is tried. When the whole pool is exhausted and a 429 was
  involved, the failure is raised as the structured QuotaExhaustedError
  so the scheduler's --loop mode pauses and retries the tick; pure
  401/403 exhaustion is a plain ProviderError (waiting cannot fix a bad
  key). ACP_<BACKEND>_BASE_URL overrides the preset endpoint (local
  proxies / relays), ACP_<BACKEND>_MIN_INTERVAL paces calls per backend.

Structured output: response_format={"type": "json_object"} plus the
explicit field/enum contract appended to the system prompt, sanitizing
coercion of frequent enum improvisations, Pydantic validation of
AgentDecision, and bounded validation-repair retries — the same
decision path as the Gemini provider. Per-vendor notes:
- DeepSeek: json_object mode requires the word "JSON" in the prompt
  (the contract text satisfies this); deepseek-chat is the cheap tier
  (deepseek-reasoner also works via ACP_MODEL but returns
  reasoning_content alongside content — only content is parsed).
- Kimi (Moonshot): moonshot-v1-8k supports json_object; the kimi-k2
  series models do NOT accept response_format on the CN endpoint —
  override with ACP_MODEL only to moonshot-v1 models for now.
- Qwen (DashScope compatible-mode): qwen-turbo supports json_object;
  some Qwen models additionally require the prompt to mention JSON
  (satisfied). Non-compatible-mode DashScope endpoints are not used.
- OpenAI: gpt-4o-mini supports json_object (json_schema strict mode
  was dropped in favour of one shared code path for all four vendors).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..protocol import AgentDecision, DecisionContext, ProviderResult
from .base import LLMProvider, ProviderError, QuotaExhaustedError

SYSTEM_TEMPLATE = """你是一个多智能体故事模拟中的独立角色。你只能依据给你的信息行动，
不知道其他角色的内心、记忆或未发生在你面前的事情。

你的身份：{identity}
你的人格（0-1）：{personality}
你的原则：{principles}
你的长期目标：{goals}

每一轮，你会收到一份 JSON 上下文（你所在的位置、你亲耳听到/亲眼看到的事件、
你的信念、情绪、当前计划、可用行动）。你必须只返回一个符合给定 JSON Schema 的
决策对象：选择一个可用行动，给出简短的 reasoning_summary（审计用，一两句话）、
expected_outcome（你预期的结果标签）和 0-1 的 confidence。
台词（action.content）必须符合你的身份、性格和你实际知道的信息；
你不知道的事情不能说得像亲眼见过。你可以拒绝、隐瞒、撒谎或什么都不做（wait）。
"""

# json_object mode carries no schema, so the field/enum contract is
# spelled out in the prompt — identical wording to the Gemini provider.
_JSON_CONTRACT = (
    "\n输出必须是且仅是一个 JSON 对象，字段为 intent, target, action{type,target,"
    "target_location,object,content,claim,topic}, reasoning_summary, "
    "expected_outcome, confidence, plan_update{status,reason,new_goal,new_steps}。"
    "\n枚举值必须严格照抄：action.type 只能是 speak/ask/confront/move/search/show/"
    "refuse/observe/listen/work/wait 之一；plan_update.status 只能是 "
    "none/continue/advance/abandon/replace 之一（没有计划变化时用 none）。")


@dataclass(frozen=True)
class BackendPreset:
    name: str            # deepseek | kimi | qwen | openai
    base_url: str        # overridable via ACP_<BACKEND>_BASE_URL
    default_model: str   # cheap tier; overridable via ACP_MODEL(_<CHAR>)
    keys_env: str        # ACP_<BACKEND>_KEYS — comma-separated raw pool
    single_key_env: str  # the vendor's conventional single-key env var


PRESETS: dict[str, BackendPreset] = {
    "deepseek": BackendPreset(
        "deepseek", "https://api.deepseek.com/v1", "deepseek-chat",
        "ACP_DEEPSEEK_KEYS", "DEEPSEEK_API_KEY"),
    "kimi": BackendPreset(
        "kimi", "https://api.moonshot.cn/v1", "moonshot-v1-8k",
        "ACP_KIMI_KEYS", "MOONSHOT_API_KEY"),
    "qwen": BackendPreset(
        "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-turbo", "ACP_QWEN_KEYS", "DASHSCOPE_API_KEY"),
    "openai": BackendPreset(
        "openai", "https://api.openai.com/v1", "gpt-4o-mini",
        "ACP_OPENAI_KEYS", "OPENAI_API_KEY"),
}

# Pool bookkeeping, shared by every agent instance of a backend (a key's
# budget belongs to the account, not to one character). Exhaustion is
# keyed by (backend, label) so backends never poison each other.
_EXHAUSTED_KEYS: set[tuple[str, str]] = set()

# Per-backend call pacing, the ACP_GEMINI_MIN_INTERVAL pattern:
# ACP_<BACKEND>_MIN_INTERVAL seconds between calls (default 0 — the
# compatible backends are paid APIs without Gemini's free-tier burst
# behaviour, but the mechanism exists for rate-limited tiers).
_PACE_LOCK = asyncio.Lock()
_PACE_LAST: dict[str, float] = {}

_ROTATE_STATUSES = (429, 401, 403)


def _interval_for(backend: str) -> float:
    return float(os.environ.get(f"ACP_{backend.upper()}_MIN_INTERVAL", "0"))


async def _pace(backend: str) -> None:
    interval = _interval_for(backend)
    if interval <= 0:
        return
    async with _PACE_LOCK:
        now = time.monotonic()
        wait = _PACE_LAST.get(backend, 0.0) + interval - now
        if wait > 0:
            await asyncio.sleep(wait)
        _PACE_LAST[backend] = time.monotonic()


def _parse_pool(preset: BackendPreset) -> list[str]:
    """Raw keys for a preset: the ACP_<BACKEND>_KEYS pool first, then
    the vendor's single-key env var. Key text is returned only here and
    stored only in the provider's private map."""
    keys = [k.strip() for k in
            os.environ.get(preset.keys_env, "").split(",") if k.strip()]
    if keys:
        return keys
    single = os.environ.get(preset.single_key_env, "").strip()
    return [single] if single else []


def keys_available(backend: str) -> bool:
    """True if the named backend has at least one key configured."""
    return bool(_parse_pool(PRESETS[backend]))


def credential_available() -> bool:
    """Back-compat helper: the openai preset's key check."""
    return keys_available("openai")


class _HttpStatusError(ProviderError):
    """Transport failure carrying the HTTP status code, so rotation and
    quota decisions key off the status — never off message text."""

    def __init__(self, status: int, backend: str, body: str):
        super().__init__(f"HTTP {status} from {backend} API: {body}")
        self.status = status


def _status_of(e: Exception) -> int | None:
    status = getattr(e, "status", None)
    if isinstance(status, int):
        return status
    m = re.match(r"HTTP (\d{3})\b", str(e))
    return int(m.group(1)) if m else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused")


class OpenAICompatibleProvider(LLMProvider):
    """One chat-completions backend, parameterized by PRESET. Directly
    instantiated it behaves as the openai preset (historical default)."""
    PRESET = PRESETS["openai"]
    name = "openai"

    def __init__(self, config, seed: int = 42, model: str | None = None,
                 temperature: float | None = None, timeout: float = 60.0,
                 max_retries: int = 3):
        super().__init__(config, seed)
        preset = self.PRESET
        self.backend = preset.name
        self.base_url = (os.environ.get(
            f"ACP_{preset.name.upper()}_BASE_URL") or preset.base_url
        ).rstrip("/")
        self.model = model or os.environ.get("ACP_MODEL") \
            or preset.default_model
        self.temperature = float(os.environ.get(
            "ACP_TEMPERATURE", temperature if temperature is not None
            else 0.7))
        self.timeout = timeout
        self.max_retries = max_retries
        keys = _parse_pool(preset)
        self.credentials = [f"key#{i + 1}" for i in range(len(keys))]
        self._raw_keys = dict(zip(self.credentials, keys))

    # ---------- transport (sync; run in a thread by the async wrapper) ----------
    def _post_chat(self, payload: dict, credential: str | None = None) -> dict:
        url = f"{self.base_url}/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"})
        label = credential or (self.credentials[0] if self.credentials
                               else None)
        if label in self._raw_keys:
            # The key text goes only into this request's header.
            req.add_header("Authorization",
                           f"Bearer {self._raw_keys[label]}")
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")[:400]
            raise _HttpStatusError(e.code, self.backend, text) from e
        except urllib.error.URLError as e:
            raise ProviderError(
                f"{self.backend} API request failed: {e.reason}") from e

    def _system_prompt(self) -> str:
        cfg = self.config
        return SYSTEM_TEMPLATE.format(
            identity=cfg.identity,
            personality=json.dumps(cfg.personality, ensure_ascii=False),
            principles="；".join(cfg.principles),
            goals="；".join(g["desc"] for g in cfg.goals)) + _JSON_CONTRACT

    @staticmethod
    def _sanitize(raw: str) -> dict:
        """Coerce frequent model improvisations into the schema's enums
        before validation (same table as the Gemini provider). Anything
        still invalid after this fails loudly and is retried — it is
        never executed."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[start:end + 1])
        action = data.get("action") or {}
        type_map = {"talk": "speak", "say": "speak", "go": "move",
                    "move_to": "move", "question": "ask",
                    "challenge": "confront", "look": "observe",
                    "search_drawer": "search", "do_nothing": "wait"}
        if action.get("type") in type_map:
            action["type"] = type_map[action["type"]]
        pu = data.get("plan_update") or {}
        status_map = {"new_plan": "replace", "create": "replace",
                      "start": "replace", "update": "continue",
                      "keep": "continue", "ongoing": "continue"}
        if pu.get("status") in status_map:
            pu["status"] = status_map[pu["status"]]
        if pu.get("status") not in ("none", "continue", "advance",
                                    "abandon", "replace"):
            pu["status"] = "none"
        data["plan_update"] = pu
        conf = data.get("confidence")
        if isinstance(conf, (int, float)):
            data["confidence"] = min(1.0, max(0.0, float(conf)))
        return data

    async def generate_decision(self, context: DecisionContext) -> ProviderResult:
        preset = self.PRESET
        if not self.credentials:
            raise ProviderError(
                f"No {self.backend} API key available: set "
                f"{preset.keys_env} (comma-separated key pool) or "
                f"{preset.single_key_env}; llm mode cannot run.")
        user_text = context.model_dump_json()
        last_error: Exception | None = None
        started = time.monotonic()
        repair_note = ""
        active = [c for c in self.credentials
                  if (self.backend, c) not in _EXHAUSTED_KEYS]
        cred_idx = 0
        repairs = 0
        saw_quota = False
        while True:
            label = active[cred_idx] if cred_idx < len(active) \
                else self.credentials[0]
            payload = {
                "model": self.model,
                "temperature": self.temperature,
                "messages": [
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": user_text + repair_note},
                ],
                "response_format": {"type": "json_object"},
            }
            try:
                await _pace(self.backend)
                data = await asyncio.to_thread(self._post_chat, payload, label)
                raw = data["choices"][0]["message"]["content"] or ""
                decision = AgentDecision.model_validate(self._sanitize(raw))
                usage = data.get("usage") or {}
                return ProviderResult(
                    decision=decision, provider=self.name, model=self.model,
                    input_tokens=usage.get("prompt_tokens", 0) or 0,
                    output_tokens=usage.get("completion_tokens", 0) or 0,
                    raw_output=raw,
                    latency_ms=int((time.monotonic() - started) * 1000))
            except Exception as e:
                status = _status_of(e)
                if status in _ROTATE_STATUSES:
                    if status == 429:
                        saw_quota = True
                    # This key answered a rotation-class status: it is
                    # done for the process, whether or not another key
                    # remains to rotate to.
                    _EXHAUSTED_KEYS.add((self.backend, label))
                    if cred_idx + 1 < len(active):
                        # Rotate: a fresh key carries a fresh budget.
                        # Rotation does not consume the repair budget.
                        cred_idx += 1
                        continue
                    # The whole pool failed on a rotation-class status.
                    if status == 429 or saw_quota:
                        raise QuotaExhaustedError(
                            f"{self.backend} quota exhausted (429): every "
                            f"pooled key is rate-limited: {e}") from e
                    raise ProviderError(
                        f"{self.backend} API rejected every pooled key "
                        f"(HTTP {status}): {e}") from e
                last_error = e
                if repairs < self.max_retries:
                    repairs += 1
                    repair_note = (
                        f"\n\n注意：上一次输出未通过校验"
                        f"（{type(e).__name__}: {e}）。"
                        f"请只返回符合要求的 JSON 对象。")
                    await asyncio.sleep(min(2 ** (repairs - 1), 4))
                    continue
                break
        raise ProviderError(
            f"{self.backend} decision failed after {self.max_retries + 1} "
            f"attempts: {type(last_error).__name__}: {last_error}")


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI preset (the historical provider class name)."""
    PRESET = PRESETS["openai"]
    name = "openai"


class DeepSeekProvider(OpenAICompatibleProvider):
    PRESET = PRESETS["deepseek"]
    name = "deepseek"


class KimiProvider(OpenAICompatibleProvider):
    PRESET = PRESETS["kimi"]
    name = "kimi"


class QwenProvider(OpenAICompatibleProvider):
    PRESET = PRESETS["qwen"]
    name = "qwen"
