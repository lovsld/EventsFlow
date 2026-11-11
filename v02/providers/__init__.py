"""Provider package: base / rule / mock / gemini / openai-compatible
backends (openai, deepseek, kimi, qwen), one interface.

Mode mapping: rule/mock are literal. "llm" resolves to the LLM backend
Hu selects: Gemini by default (chosen 2026-11-06), or one of the
OpenAI-compatible backends via --provider / ACP_PROVIDER (the legacy
ACP_LLM_PROVIDER env var is still honoured as an alias). Each backend
carries its own raw key pool — see openai_provider.py / LOCAL_DEPLOY.md.
"""
import os

from .base import LLMProvider, ProviderError
from .gemini_provider import GeminiProvider
from .mock_provider import MockProvider
from .openai_provider import (DeepSeekProvider, KimiProvider,
                              OpenAIProvider, QwenProvider)
from .rule_provider import RuleProvider

PROVIDERS = {"rule": RuleProvider, "mock": MockProvider,
             "openai": OpenAIProvider, "gemini": GeminiProvider,
             "deepseek": DeepSeekProvider, "kimi": KimiProvider,
             "qwen": QwenProvider}

LLM_BACKENDS = ("gemini", "deepseek", "kimi", "qwen", "openai")
DEFAULT_BACKEND = "gemini"


def requested_backend(provider: str | None = None) -> str | None:
    """The explicitly requested llm backend, or None if the caller did
    not choose one: CLI --provider first, then ACP_PROVIDER, then the
    legacy ACP_LLM_PROVIDER alias."""
    return provider or os.environ.get("ACP_PROVIDER") \
        or os.environ.get("ACP_LLM_PROVIDER") or None


def resolve_backend(provider: str | None = None) -> str:
    """The effective llm backend: the requested one, else the default."""
    backend = requested_backend(provider) or DEFAULT_BACKEND
    if backend not in LLM_BACKENDS:
        raise ValueError(f"unknown llm backend {backend!r}, expected one "
                         f"of {list(LLM_BACKENDS)}")
    return backend


def make_provider(mode: str, config, seed: int = 42,
                  provider: str | None = None) -> LLMProvider:
    if mode == "llm":
        mode = resolve_backend(provider)
    if mode not in PROVIDERS:
        raise ValueError(f"unknown mode {mode!r}, expected one of "
                         f"{sorted(PROVIDERS)} or 'llm'")
    if mode in LLM_BACKENDS:
        # Per-character model override (ACP_MODEL_OWNER / _MERCHANT /
        # _SOLDIER) lets a long run spread its calls across models, each
        # with its own budget; ACP_MODEL remains the shared fallback.
        # (Added 2026-11-10 for Gemini; generalized to every backend
        # with the multi-backend pool.)
        model = (os.environ.get(f"ACP_MODEL_{config.character_id.upper()}")
                 or os.environ.get("ACP_MODEL"))
        if model:
            return PROVIDERS[mode](config, seed=seed, model=model)
    return PROVIDERS[mode](config, seed=seed)
