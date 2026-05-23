"""Ollama provider — thin shim over OpenAICompatProvider.

Ollama exposes an OpenAI-compatible Chat Completions endpoint at
`/v1/chat/completions`.  All translation logic lives in `openai_compat.py`;
this file picks defaults (base_url, model capability flags) and runs a
warn-only preflight against `/api/tags` so users get a clear hint when
they've named a model they haven't pulled.

See _design/0014-ollama-provider.md.
"""
from __future__ import annotations

import json
import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

from arc.config import ProviderConfig
from arc.providers.openai_compat import (
    CompatCapabilities,
    OpenAICompatProvider,
    init_from_provider_config,
)

log = logging.getLogger("arc.providers.ollama")


DEFAULT_BASE_URL = "http://localhost:11434/v1"

# Model families known to ship with tool-use support in their chat template.
# Conservative — if a family isn't here, we default to tool_use=False and the
# shim raises a clear error at first chat() if the user has tools enabled.
_TOOL_CAPABLE_FAMILIES = (
    "llama3.1",
    "llama3.2",
    "llama3.3",
    "hermes3",
    "mistral-nemo",
    "mistral-large",
    "qwen2.5",
    "qwen3",
    "command-r",
    "firefunction",
    "granite3",
)


def _capabilities_for(model: str) -> CompatCapabilities:
    """Choose capability flags for a given Ollama model id."""
    name = (model or "").lower()
    tool_use = any(fam in name for fam in _TOOL_CAPABLE_FAMILIES)
    return CompatCapabilities(
        tool_use=tool_use,
        parallel_tool_calls=tool_use,
        json_mode=True,
        json_schema=False,
    )


class OllamaProvider(OpenAICompatProvider):
    """Ollama-flavored OpenAICompatProvider.

    The only thing it adds on top of the shared shim is:
      - Sensible defaults for base_url and api_key
      - Capability detection based on model name
      - A preflight check that warns (doesn't fail) if the chosen model
        isn't pulled
    """

    name = "ollama"

    def __init__(self, cfg: ProviderConfig) -> None:
        kwargs = init_from_provider_config(
            cfg,
            default_base_url=DEFAULT_BASE_URL,
            # Ollama doesn't validate the api key but the openai SDK requires
            # a non-empty string. Real Ollama servers ignore the value.
            default_api_key_env_value="ollama",
            capabilities=_capabilities_for(cfg.model),
        )
        super().__init__(**kwargs)
        _preflight(self._base_url, cfg.model)


def _preflight(base_url: str, model: str) -> None:
    """Hit `/api/tags` and warn if the named model isn't in the local cache.

    All failures are swallowed — the server may simply not be running yet,
    or be behind a proxy that doesn't expose `/api/tags`.  We don't want to
    crash startup on a probe.
    """
    api_root = base_url.rstrip("/")
    if api_root.endswith("/v1"):
        api_root = api_root[: -len("/v1")]
    url = f"{api_root}/api/tags"

    try:
        req = Request(url, headers={"User-Agent": "arc-cli/1"})
        with urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError, TimeoutError):
        return  # Server unreachable / bad response — let chat() surface real errors.

    pulled = {m.get("name", "") for m in body.get("models", []) if isinstance(m, dict)}
    if not pulled:
        return  # Server up but reports no models — nothing useful to compare.

    candidates = {model, f"{model}:latest"}
    if not (candidates & pulled):
        log.warning(
            "ollama: model %r not in local cache at %s.  "
            "Run `ollama pull %s` first.  "
            "(Continuing — Ollama may lazy-pull but the first turn will be slow.)",
            model, base_url, model,
        )


__all__ = ["OllamaProvider", "DEFAULT_BASE_URL", "_capabilities_for"]
