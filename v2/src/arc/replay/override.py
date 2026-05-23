"""Provider/model override helpers for cross-provider replay (0019).

Given a loaded `Config` (from the source session's `config.snapshot.yml`)
and a (provider, model) target, return a new Config where the
`provider:` block is swapped to the target while everything else
(retry, params, plugins, tools) is preserved.

Defaults for `api_key_env` and `base_url` mirror the 0017 picker's table
so picking 'ollama' auto-fills `OLLAMA_API_KEY` + `http://localhost:11434/v1`
without the caller having to know.
"""
from __future__ import annotations

from dataclasses import replace

from arc.config import Config, ProviderConfig


# Mirrors arc.setup.picker._PROVIDER_DEFAULTS without taking a dep on it
# (replay/ shouldn't depend on setup/).
_PROVIDER_DEFAULTS: dict[str, dict[str, str | None]] = {
    "anthropic": {"api_key_env": "ANTHROPIC_API_KEY", "base_url": None},
    "gemini": {"api_key_env": "GEMINI_API_KEY", "base_url": None},
    "ollama": {"api_key_env": "OLLAMA_API_KEY",
               "base_url": "http://localhost:11434/v1"},
    "llama_cpp": {"api_key_env": "LLAMA_CPP_API_KEY",
                  "base_url": "http://localhost:8080/v1"},
}


class OverrideError(ValueError):
    """The requested provider/model can't be applied to the source config."""


def known_providers() -> list[str]:
    return list(_PROVIDER_DEFAULTS.keys())


def apply_override(cfg: Config, *, provider: str, model: str) -> Config:
    """Return a new Config with provider.name + provider.model swapped.

    api_key_env and base_url are auto-set to the provider's defaults so
    callers don't have to remember them.  Retry policy, params, tools,
    plugins are preserved verbatim.

    Raises OverrideError if the provider name isn't in the known set.
    """
    if provider not in _PROVIDER_DEFAULTS:
        known = ", ".join(_PROVIDER_DEFAULTS.keys())
        raise OverrideError(
            f"unknown provider {provider!r}.  known: {known}"
        )

    defaults = _PROVIDER_DEFAULTS[provider]
    new_provider = ProviderConfig(
        name=provider,
        model=model,
        api_key_env=defaults["api_key_env"] or cfg.provider.api_key_env,
        base_url=defaults.get("base_url"),
        timeout_seconds=cfg.provider.timeout_seconds,
        retry=cfg.provider.retry,
        params=dict(cfg.provider.params),
    )
    return replace(cfg, provider=new_provider)


def parse_target(spec: str) -> tuple[str, str]:
    """Parse a `provider:model` string used by `--against` and similar.

    Returns (provider, model).  Raises OverrideError with a clear message
    on malformed input.
    """
    if ":" not in spec:
        raise OverrideError(
            f"invalid target {spec!r}: expected 'provider:model' "
            f"(e.g. 'ollama:llama3.1:8b' or 'anthropic:claude-haiku-4-5')"
        )
    # Ollama uses colons in model tags ("llama3.1:8b"), so split at the first
    # colon only — provider can't contain colons.
    provider, _, model = spec.partition(":")
    provider = provider.strip()
    model = model.strip()
    if not provider or not model:
        raise OverrideError(f"invalid target {spec!r}: empty provider or model")
    if provider not in _PROVIDER_DEFAULTS:
        raise OverrideError(
            f"target {spec!r}: unknown provider {provider!r}.  "
            f"known: {', '.join(_PROVIDER_DEFAULTS)}"
        )
    return (provider, model)


def parse_target_list(spec: str) -> list[tuple[str, str]]:
    """Parse a comma-separated `--against` value into N targets."""
    targets: list[tuple[str, str]] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        targets.append(parse_target(chunk))
    if not targets:
        raise OverrideError("empty target list")
    return targets
