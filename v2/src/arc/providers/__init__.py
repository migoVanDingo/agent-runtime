"""Provider factory.

The factory is the runtime's only contact point with concrete provider types.
Adding a new provider = adding a class here + a case in `build()`.
"""
from __future__ import annotations

from arc.config import ProviderConfig
from arc.providers.base import LLMProvider


def build(cfg: ProviderConfig) -> LLMProvider:
    """Construct the provider named in config.provider.name.

    Unknown provider names raise a clear error at startup.
    """
    if cfg.name == "gemini":
        from arc.providers.gemini import GeminiProvider
        return GeminiProvider(cfg)
    if cfg.name == "anthropic":
        from arc.providers.anthropic import AnthropicProvider
        return AnthropicProvider(cfg)
    if cfg.name == "ollama":
        from arc.providers.ollama import OllamaProvider
        return OllamaProvider(cfg)
    if cfg.name == "llama_cpp":
        from arc.providers.llama_cpp import LlamaCppProvider
        return LlamaCppProvider(cfg)

    raise ValueError(
        f"unknown provider {cfg.name!r}\n"
        f"  known: 'gemini', 'anthropic', 'ollama', 'llama_cpp'\n"
        f"  (add a case in arc/providers/__init__.py to support more)"
    )
