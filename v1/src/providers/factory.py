"""Provider factory.

Resolution order for provider and model:
  1. Explicit arguments to get_provider() — highest priority.
  2. config.yml [llm.provider] / [llm.model]
  3. Deprecated .env LLM_PROVIDER / *_MODEL overrides (backwards compat).
  4. Hard defaults in Settings.

This lets config.yml be the single place you change when switching models,
while .env stays for keys only.
"""
from providers.base import BaseProvider
from providers.anthropic import AnthropicProvider
from providers.ollama import OllamaProvider
from providers.openai_provider import OpenAIProvider
from providers.grok import GrokProvider
from providers.deepseek import DeepSeekProvider
from providers.gemini import GeminiProvider
from settings import get_settings


def _provider_defaults() -> tuple[str, dict[str, str]]:
    """Return (resolved_provider_name, per-provider default models).

    Merges config.yml [llm] section with .env legacy overrides.
    config.yml wins; .env is fallback.
    """
    settings = get_settings()
    try:
        from app_config import config
        cfg_provider = config.llm.provider or ""
        cfg_models: dict[str, str | None] = {
            "anthropic": config.llm.model if cfg_provider == "anthropic" else None,
            "openai":    config.llm.model if cfg_provider == "openai"    else None,
            "grok":      config.llm.model if cfg_provider == "grok"      else None,
            "deepseek":  config.llm.model if cfg_provider == "deepseek"  else None,
            "gemini":    config.llm.model if cfg_provider == "gemini"    else None,
            "ollama":    config.llm.model if cfg_provider == "ollama"    else None,
        }
    except Exception:
        cfg_provider = ""
        cfg_models = {}

    # .env legacy overrides (only used when config.yml doesn't specify a model)
    env_models: dict[str, str | None] = {
        "anthropic": settings.anthropic_model,
        "openai":    settings.openai_model,
        "grok":      settings.grok_model,
        "deepseek":  settings.deepseek_model,
        "gemini":    settings.gemini_model,
        "ollama":    settings.ollama_model,
    }

    # Hard defaults when nothing is specified anywhere
    hard_defaults: dict[str, str] = {
        "anthropic": "claude-3-5-haiku-latest",
        "openai":    "gpt-4o-mini",
        "grok":      "grok-3-mini",
        "deepseek":  "deepseek-chat",
        "gemini":    "gemini-2.0-flash",
        "ollama":    "llama3.2",
    }

    resolved_provider = (
        cfg_provider
        or settings.llm_provider
        or "anthropic"
    )

    # Build merged model map: config.yml > .env > hard default
    merged: dict[str, str] = {}
    for p in hard_defaults:
        merged[p] = (
            cfg_models.get(p)
            or env_models.get(p)
            or hard_defaults[p]
        )

    return resolved_provider, merged


def get_provider(provider_name: str | None = None, model_override: str | None = None) -> BaseProvider:
    """Build a provider instance.

    Args:
        provider_name: explicit provider name, or None to use config.yml default.
        model_override: explicit model, or None to use config.yml / .env default.
    """
    settings = get_settings()
    resolved_provider, default_models = _provider_defaults()
    name = provider_name or resolved_provider

    def model(p: str) -> str:
        return model_override or default_models.get(p, "")

    if name == "anthropic":
        return AnthropicProvider(api_key=settings.anthropic_api_key, model=model("anthropic"))

    if name == "openai":
        return OpenAIProvider(api_key=settings.openai_api_key, model=model("openai"))

    if name == "ollama":
        return OllamaProvider(base_url=settings.ollama_base_url, model=model("ollama"))

    if name == "grok":
        return GrokProvider(api_key=settings.grok_api_key, model=model("grok"))

    if name == "deepseek":
        return DeepSeekProvider(api_key=settings.deepseek_api_key, model=model("deepseek"))

    if name == "gemini":
        return GeminiProvider(api_key=settings.gemini_api_key, model=model("gemini"))

    raise ValueError(
        f"Unknown LLM provider: '{name}'. "
        f"Expected 'anthropic', 'openai', 'ollama', 'grok', 'deepseek', or 'gemini'."
    )


def get_runtime_provider() -> BaseProvider:
    """Build the provider for runtime infrastructure calls.

    Resolution order:
      1. config.yml [llm.runtime_provider] / [llm.runtime_model]
      2. .env RUNTIME_PROVIDER / RUNTIME_MODEL (legacy)
      3. Fall back to main provider.
    """
    settings = get_settings()

    try:
        from app_config import config
        cfg_rt_provider = config.llm.runtime_provider
        cfg_rt_model = config.llm.runtime_model
    except Exception:
        cfg_rt_provider = None
        cfg_rt_model = None

    rt_provider = cfg_rt_provider or settings.runtime_provider
    rt_model = cfg_rt_model or settings.runtime_model

    if rt_provider:
        return get_provider(rt_provider, rt_model)

    return get_provider()
