"""Thin httpx wrapper around llama.cpp's native `/completion` endpoint.

Compat mode uses the OpenAI SDK directly (via OpenAICompatProvider); this
module is used only by grammar mode, which needs a richer request shape
than `/v1/chat/completions` supports (specifically the `grammar` param).
"""
from __future__ import annotations

from typing import Any


def post_completion(
    *,
    base_url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    """POST to `{base_url}/completion`, return the parsed JSON body.

    Strips a trailing `/v1` from base_url if present so `compat-style`
    configs that include the OpenAI path prefix still hit the right URL.
    """
    import httpx

    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    url = f"{root}/completion"

    resp = httpx.post(url, json=payload, timeout=timeout_seconds)
    resp.raise_for_status()
    return resp.json()


def get_health(*, base_url: str, timeout_seconds: float = 3.0) -> dict[str, Any] | None:
    """Probe `/health`; return the parsed body or None on any failure."""
    import httpx

    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    try:
        resp = httpx.get(f"{root}/health", timeout=timeout_seconds)
        if resp.status_code >= 400:
            return None
        return resp.json()
    except Exception:
        return None
