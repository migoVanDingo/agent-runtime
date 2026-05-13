"""LLM provider configuration dataclass."""
from dataclasses import dataclass


@dataclass
class LLMConfig:
    max_tokens: int
    # Main agent LLM
    provider: str = "anthropic"
    model: str | None = None
    # Runtime LLM (classifier, monitor, importance, council)
    runtime_provider: str | None = None
    runtime_model: str | None = None
    # Sampling temperature (telemetry-exposed; providers may ignore if unset).
    temperature: float | None = None
