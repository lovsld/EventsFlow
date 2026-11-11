"""LLM Provider layer (task book section 5).

One provider instance per character. A provider only ever receives that
character's DecisionContext; it has no access to the world, the database,
or other agents. Switching providers must not require Agent Runtime changes.
"""
from __future__ import annotations

from ..config import CharacterConfig
from ..protocol import DecisionContext, ProviderResult


class ProviderError(Exception):
    """Any provider failure (missing key, timeout, rate limit, bad output).
    The runtime treats this as 'no decision this tick' — the world is never
    modified by a failed call."""


class QuotaExhaustedError(ProviderError):
    """Structured quota signal: the provider's whole credential pool is
    exhausted (every key answered 429). The scheduler's --loop mode keys
    off this TYPE — never off message text — to pause and retry the same
    tick instead of burning it on a degraded wait. Ordinary failures
    (500s, timeouts, parse errors) stay plain ProviderError and keep the
    existing degrade-to-wait semantics."""


class LLMProvider:
    name: str = "base"
    model: str | None = None

    def __init__(self, config: CharacterConfig, seed: int = 42):
        self.config = config
        self.seed = seed

    async def generate_decision(self, context: DecisionContext) -> ProviderResult:
        raise NotImplementedError
