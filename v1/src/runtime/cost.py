"""Central pricing table for LLM cost telemetry.

Rates are expressed in USD per 1M tokens for both input and output. When a
model is not in the table, ``compute_cost`` returns None — analysts will see
NaN in ``cost_usd`` rather than a fabricated figure.

Rates here should be sourced from each provider's published pricing page and
updated when prices change. Pricing changes do not require provider code
edits — only this table.

Cache rates (when supported by the provider):
- ``cache_read_in_per_m`` is what the provider charges to *read* from a cached
  prefix. Anthropic charges ~10% of base input. OpenAI does not separately
  meter cache reads as of the last refresh (counted as regular input).
- ``cache_creation_in_per_m`` is the surcharge to *write* a new prefix into
  cache. Anthropic charges 1.25x base input.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelRates:
    input_per_m: float           # USD per 1M input tokens
    output_per_m: float          # USD per 1M output tokens
    cache_read_in_per_m: float | None = None
    cache_creation_in_per_m: float | None = None


# Keys are matched by *prefix* (longest match wins) so e.g. ``claude-sonnet-4-6``
# and ``claude-sonnet-4-6-20251022`` resolve to the same entry.
PRICING: dict[str, ModelRates] = {
    # ── Anthropic ─────────────────────────────────────────────────────
    "claude-opus-4-7":     ModelRates(15.00, 75.00, 1.50, 18.75),
    "claude-opus-4-6":     ModelRates(15.00, 75.00, 1.50, 18.75),
    "claude-opus-4":       ModelRates(15.00, 75.00, 1.50, 18.75),
    "claude-sonnet-4-6":   ModelRates(3.00,  15.00, 0.30, 3.75),
    "claude-sonnet-4-5":   ModelRates(3.00,  15.00, 0.30, 3.75),
    "claude-sonnet-4":     ModelRates(3.00,  15.00, 0.30, 3.75),
    "claude-haiku-4-5":    ModelRates(1.00,   5.00, 0.10, 1.25),
    "claude-3-5-haiku":    ModelRates(0.80,   4.00, 0.08, 1.00),
    "claude-3-5-sonnet":   ModelRates(3.00,  15.00, 0.30, 3.75),
    "claude-3-opus":       ModelRates(15.00, 75.00, 1.50, 18.75),

    # ── OpenAI ────────────────────────────────────────────────────────
    "gpt-4o-mini":         ModelRates(0.15, 0.60),
    "gpt-4o":              ModelRates(2.50, 10.00),
    "gpt-4.1-mini":        ModelRates(0.40, 1.60),
    "gpt-4.1":             ModelRates(2.00, 8.00),
    "o1-mini":             ModelRates(3.00, 12.00),
    "o1":                  ModelRates(15.00, 60.00),

    # ── Google Gemini ────────────────────────────────────────────────
    "gemini-2.5-pro":      ModelRates(1.25, 10.00),
    "gemini-2.5-flash":    ModelRates(0.15, 0.60),
    "gemini-2.0-flash":    ModelRates(0.10, 0.40),

    # ── DeepSeek ─────────────────────────────────────────────────────
    "deepseek-chat":       ModelRates(0.27, 1.10),
    "deepseek-reasoner":   ModelRates(0.55, 2.19),

    # ── xAI Grok ─────────────────────────────────────────────────────
    "grok-4":              ModelRates(3.00, 15.00),
    "grok-4-1-fast":       ModelRates(0.20, 0.50),
    "grok-3":              ModelRates(3.00, 15.00),
}


def _resolve(model: str) -> ModelRates | None:
    """Return rates for the longest prefix of PRICING that matches ``model``."""
    if not model:
        return None
    best_key: str | None = None
    for key in PRICING:
        if model.startswith(key) and (best_key is None or len(key) > len(best_key)):
            best_key = key
    return PRICING[best_key] if best_key else None


def compute_cost(
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_input_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
) -> float | None:
    """Compute the USD cost of a single LLM call. Returns None if model unknown.

    Input tokens already exclude cache_input_tokens (regular billed input).
    For Anthropic responses where input_tokens reflects non-cached portion:
        total = (input_tokens * input_per_m + cache_input * cache_read_per_m
                 + cache_create * cache_creation_per_m + output * output_per_m) / 1e6
    """
    if not model:
        return None
    rates = _resolve(model)
    if rates is None:
        return None
    total = 0.0
    if input_tokens:
        total += input_tokens * rates.input_per_m
    if output_tokens:
        total += output_tokens * rates.output_per_m
    if cache_input_tokens and rates.cache_read_in_per_m is not None:
        total += cache_input_tokens * rates.cache_read_in_per_m
    elif cache_input_tokens:
        # Provider doesn't price cache reads separately — charge as regular input.
        total += cache_input_tokens * rates.input_per_m
    if cache_creation_tokens and rates.cache_creation_in_per_m is not None:
        total += cache_creation_tokens * rates.cache_creation_in_per_m
    return total / 1_000_000.0
