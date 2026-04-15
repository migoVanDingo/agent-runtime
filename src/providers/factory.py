from providers.base import BaseProvider
from providers.anthropic import AnthropicProvider
from providers.ollama import OllamaProvider
from providers.openai_provider import OpenAIProvider
from settings import get_settings


def get_provider(provider_name: str | None = None, model_override: str | None = None) -> BaseProvider:
    """Build a provider instance.

    Args:
        provider_name: "anthropic", "openai", or "ollama".
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

    raise ValueError(
        f"Unknown LLM provider: '{name}'. Expected 'anthropic', 'openai', or 'ollama'."
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
