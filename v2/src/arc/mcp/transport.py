"""Transport factories over the `mcp` SDK.

Each returns the SDK's async context manager for a server's byte streams. stdio
yields (read, write); streamable-HTTP yields (read, write, get_session_id) — the
manager's actor takes the first two either way.

The SDK is imported lazily so `arc.mcp` imports without the `mcp` extra
installed; `require_sdk()` raises a clear error at connect time if it's missing.
"""
from __future__ import annotations

from typing import Any

from arc.mcp.config import McpServerConfig


class McpSdkMissing(RuntimeError):
    """The `mcp` SDK is not installed."""


def require_sdk() -> None:
    try:
        import mcp  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise McpSdkMissing(
            "the `mcp` SDK is not installed; `pip install \"arc[mcp]\"` (or `pip install mcp`) "
            "to enable MCP servers"
        ) from exc


def open_transport(cfg: McpServerConfig) -> Any:
    """Return the async context manager yielding (read, write[, ...]) streams."""
    require_sdk()
    if cfg.transport == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=cfg.command[0],
            args=list(cfg.command[1:]),
            env=cfg.env or None,
            cwd=cfg.cwd,
        )
        return stdio_client(params)

    if cfg.transport == "http":
        import mcp.client.streamable_http as sh

        # SDK renamed streamablehttp_client -> streamable_http_client; prefer the
        # new name, fall back to the old for older SDK pins.
        factory = getattr(sh, "streamable_http_client", None) or sh.streamablehttp_client
        return factory(cfg.url)

    raise ValueError(f"unknown transport: {cfg.transport}")  # pragma: no cover — validated upstream
