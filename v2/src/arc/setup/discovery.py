"""Live model discovery for local providers used by the `arc setup` picker.

Ollama exposes `/api/tags`; llama-server exposes `/v1/models`.  Both are
HTTP GETs that return a JSON listing.  We surface failures as empty
lists plus a reason string — the picker falls back to the manual-entry
sentinel when discovery doesn't return anything.

See _design/0017-provider-picker.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("arc.setup.discovery")


@dataclass(frozen=True)
class DiscoveredModel:
    """One model the live server reports as available."""
    id: str
    label: str
    note: str = ""


@dataclass(frozen=True)
class DiscoveryResult:
    """What we found (or didn't) at a live endpoint."""
    models: list[DiscoveredModel]
    reason: str = ""  # human-readable; non-empty when models is empty


def fetch_ollama_models(base_url: str, *, timeout: float = 5.0) -> DiscoveryResult:
    """Query an Ollama server's `/api/tags`; return what it reports.

    `base_url` may end in `/v1` (the chat-completions root); we strip it
    before hitting the native tags endpoint.
    """
    import httpx

    root = _strip_v1(base_url)
    url = f"{root}/api/tags"
    try:
        resp = httpx.get(url, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        return DiscoveryResult(models=[], reason=f"couldn't reach Ollama at {url}: {exc}")

    from arc.providers.ollama import _capabilities_for  # reuses 0014 capability map

    models: list[DiscoveredModel] = []
    for m in body.get("models") or []:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or ""
        if not name:
            continue
        size_bytes = m.get("size") or 0
        size_gb = float(size_bytes) / 1e9 if size_bytes else 0.0
        has_tools = _capabilities_for(name).tool_use
        bits: list[str] = []
        if size_gb >= 0.1:
            bits.append(f"{size_gb:.1f} GB")
        bits.append("tools ✓" if has_tools else "tools ✗")
        note = ", ".join(bits)
        models.append(DiscoveredModel(id=name, label=name, note=note))
    return DiscoveryResult(models=models)


def fetch_llama_cpp_models(base_url: str, *, timeout: float = 5.0) -> DiscoveryResult:
    """Query a llama-server's `/v1/models`; return what it reports.

    llama-server typically has exactly one model loaded at a time;
    treat anything it returns as the canonical pickable list.
    """
    import httpx

    base = base_url.rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    url = f"{base}/models"
    try:
        resp = httpx.get(url, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        return DiscoveryResult(models=[], reason=f"couldn't reach llama-server at {url}: {exc}")

    models: list[DiscoveredModel] = []
    for m in body.get("data") or []:
        if not isinstance(m, dict):
            continue
        mid = m.get("id") or ""
        if not mid:
            continue
        models.append(DiscoveredModel(id=mid, label=mid, note="loaded"))
    return DiscoveryResult(models=models)


# ── Helpers ────────────────────────────────────────────────────────────────


def _strip_v1(base_url: str) -> str:
    """Turn `http://host:port/v1` into `http://host:port` for native endpoints."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base
