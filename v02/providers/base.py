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


class LLMProvider:
    name: str = "base"
    model: str | None = None

    def __init__(self, config: CharacterConfig, seed: int = 42):
        self.config = config
        self.seed = seed

    async def generate_decision(self, context: DecisionContext) -> ProviderResult:
        raise NotImplementedError
