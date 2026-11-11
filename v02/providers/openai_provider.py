"""OpenAI Provider — real LLM decisions (task book section 5).

- API key from environment only (OPENAI_API_KEY); never from files or code.
- Structured output: the AgentDecision JSON schema is sent as
  response_format; the reply is validated with Pydantic before anything
  else sees it. One validation-repair retry is allowed; after that the call
  fails with ProviderError and the agent simply does not act this tick.
- Timeout, bounded retries with backoff, token usage reporting.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

from ..protocol import AgentDecision, DecisionContext, ProviderResult
from .base import LLMProvider, ProviderError

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


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, config, seed: int = 42, model: str | None = None,
                 temperature: float | None = None, timeout: float = 30.0,
                 max_retries: int = 2):
        super().__init__(config, seed)
        self.model = model or os.environ.get("ACP_MODEL", "gpt-4o-mini")
        self.temperature = float(os.environ.get("ACP_TEMPERATURE", temperature
                                                if temperature is not None else 0.7))
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = None

    def _get_client(self):
        if not os.environ.get("OPENAI_API_KEY"):
            raise ProviderError(
                "OPENAI_API_KEY is not set; llm mode cannot run. "
                "Provide the key via the environment (never in code/files).")
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(timeout=self.timeout)
        return self._client

    def _system_prompt(self) -> str:
        cfg = self.config
        return SYSTEM_TEMPLATE.format(
            identity=cfg.identity, personality=json.dumps(cfg.personality, ensure_ascii=False),
            principles="；".join(cfg.principles),
            goals="；".join(g["desc"] for g in cfg.goals))

    async def generate_decision(self, context: DecisionContext) -> ProviderResult:
        client = self._get_client()
        schema = AgentDecision.model_json_schema()
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": context.model_dump_json()},
        ]
        last_error: Exception | None = None
        started = time.monotonic()
        for attempt in range(self.max_retries + 1):
            try:
                resp = await client.chat.completions.create(
                    model=self.model, temperature=self.temperature, messages=messages,
                    response_format={"type": "json_schema",
                                     "json_schema": {"name": "AgentDecision",
                                                     "schema": schema, "strict": False}},
                )
                raw = resp.choices[0].message.content or ""
                decision = AgentDecision.model_validate_json(raw)
                usage = resp.usage
                return ProviderResult(
                    decision=decision, provider=self.name, model=self.model,
                    input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    raw_output=raw, latency_ms=int((time.monotonic() - started) * 1000))
            except ProviderError:
                raise
            except Exception as e:  # validation or API error: one repair path, then fail
                last_error = e
                if attempt < self.max_retries:
                    messages = messages + [
                        {"role": "user",
                         "content": f"上次输出未通过校验：{type(e).__name__}: {e}。"
                                    f"请只返回符合 Schema 的 JSON 对象，不要附加任何文字。"}]
                    await asyncio.sleep(min(2 ** attempt, 4))
        raise ProviderError(f"OpenAI decision failed after {self.max_retries + 1} attempts: "
                            f"{type(last_error).__name__}: {last_error}")
