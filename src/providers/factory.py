from providers.base import BaseProvider
from providers.anthropic import AnthropicProvider
from providers.ollama import OllamaProvider
from providers.openai_provider import OpenAIProvider
from providers.grok import GrokProvider
from providers.deepseek import DeepSeekProvider
from providers.gemini import GeminiProvider
from settings import get_settings


def get_provider(provider_name: str | None = None, model_override: str | None = None) -> BaseProvider:
    """Build a provider instance.

    Args:
        provider_name: "anthropic", "openai", "ollama", "grok", "deepseek", or "gemini".
                       None = use settings.llm_provider (the main provider).
        model_override: Override the default model for this provider.
                        None = use the provider's default from settings.
    """
    settings = get_settings()
    name = provider_name or settings.llm_provider

    if name == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=model_override or settings.anthropic_model,
        )

    if name == "openai":
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=model_override or settings.openai_model,
        )

    if name == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_base_url,
            model=model_override or settings.ollama_model,
        )

    if name == "grok":
        return GrokProvider(
            api_key=settings.grok_api_key,
            model=model_override or settings.grok_model,
        )

    if name == "deepseek":
        return DeepSeekProvider(
            api_key=settings.deepseek_api_key,
            model=model_override or settings.deepseek_model,
        )

    if name == "gemini":
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=model_override or settings.gemini_model,
        )

    raise ValueError(
        f"Unknown LLM provider: '{name}'. "
        f"Expected 'anthropic', 'openai', 'ollama', 'grok', 'deepseek', or 'gemini'."
    )


def get_runtime_provider() -> BaseProvider:
    """Build the provider for runtime infrastructure calls (classifier, monitor).

    Uses RUNTIME_PROVIDER / RUNTIME_MODEL from settings if set.
    Falls back to the main provider if not configured.
    """
    settings = get_settings()

    if settings.runtime_provider:
        return get_provider(settings.runtime_provider, settings.runtime_model)

    return get_provider()
