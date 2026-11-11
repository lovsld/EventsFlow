"""Provider package: base / rule / mock / openai / gemini, one interface.

Mode mapping: rule/mock are literal. "llm" resolves to the LLM provider Hu
selected: Gemini by default (chosen 2026-11-06); set ACP_LLM_PROVIDER=openai
to use the OpenAI provider instead.
"""
import os

from .base import LLMProvider, ProviderError
from .gemini_provider import GeminiProvider
from .mock_provider import MockProvider
from .openai_provider import OpenAIProvider
from .rule_provider import RuleProvider

PROVIDERS = {"rule": RuleProvider, "mock": MockProvider,
             "openai": OpenAIProvider, "gemini": GeminiProvider}


def make_provider(mode: str, config, seed: int = 42) -> LLMProvider:
    if mode == "llm":
        mode = os.environ.get("ACP_LLM_PROVIDER", "gemini")
    if mode not in PROVIDERS:
        raise ValueError(f"unknown mode {mode!r}, expected one of "
                         f"{sorted(PROVIDERS)} or 'llm'")
    if mode == "gemini":
        # Per-character model override (ACP_MODEL_OWNER / _MERCHANT /
        # _SOLDIER) lets a long run spread its calls across models, each
        # with its own free-tier daily budget; ACP_MODEL remains the
        # shared fallback. Added 2026-11-10 for the full 20-tick run.
        model = (os.environ.get(f"ACP_MODEL_{config.character_id.upper()}")
                 or os.environ.get("ACP_MODEL"))
        if model:
            return GeminiProvider(config, seed=seed, model=model)
    return PROVIDERS[mode](config, seed=seed)
