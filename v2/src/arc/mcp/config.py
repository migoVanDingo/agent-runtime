"""Parse the `mcp` plugin's config block into typed server specs.

Lives here (not in arc/config.py) because MCP is a built-in plugin — its config
is the plugin's own `entry.config` dict, parsed at build time, not a top-level
config section. See _deviations/0001.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TRANSPORTS = ("stdio", "http")


class McpConfigError(ValueError):
    """Malformed mcp plugin config."""


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    transport: str
    enabled: bool = True
    # None = unset (fall back to the server name). An explicit "" means "no
    # prefix" — tools keep their native names (e.g. cos's `container_run`).
    tool_prefix: str | None = None
    tools_allow: tuple[str, ...] = ()
    tools_deny: tuple[str, ...] = ()
    # stdio
    command: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    # http
    url: str = ""

    @property
    def prefix(self) -> str:
        return self.name if self.tool_prefix is None else self.tool_prefix


@dataclass(frozen=True)
class McpConfig:
    servers: tuple[McpServerConfig, ...] = ()
    failure_threshold: int = 3
    call_timeout_s: int = 30

    def active(self) -> tuple[McpServerConfig, ...]:
        return tuple(s for s in self.servers if s.enabled)


def parse_mcp_config(d: dict | None) -> McpConfig:
    """Parse the plugin config dict. Tolerant/default-on-missing."""
    d = d or {}
    servers: list[McpServerConfig] = []
    for i, raw in enumerate(d.get("servers") or []):
        if not isinstance(raw, dict):
            raise McpConfigError(f"mcp.servers[{i}] must be a mapping")
        name = str(raw.get("name", "")).strip()
        if not name:
            raise McpConfigError(f"mcp.servers[{i}] missing `name`")
        transport = str(raw.get("transport", "")).strip()
        if transport not in TRANSPORTS:
            raise McpConfigError(
                f"mcp server {name!r}: transport must be one of {TRANSPORTS}, got {transport!r}"
            )
        if transport == "stdio" and not raw.get("command"):
            raise McpConfigError(f"mcp server {name!r}: transport=stdio requires `command`")
        if transport == "http" and not raw.get("url"):
            raise McpConfigError(f"mcp server {name!r}: transport=http requires `url`")
        servers.append(
            McpServerConfig(
                name=name,
                transport=transport,
                enabled=bool(raw.get("enabled", True)),
                tool_prefix=(None if raw.get("tool_prefix") is None
                             else str(raw.get("tool_prefix"))),
                tools_allow=tuple(raw.get("tools_allow") or ()),
                tools_deny=tuple(raw.get("tools_deny") or ()),
                command=tuple(raw.get("command") or ()),
                env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
                cwd=raw.get("cwd"),
                url=str(raw.get("url", "") or ""),
            )
        )
    # Duplicate-name guard (would collide on tool prefixes / status rows).
    seen: set[str] = set()
    for s in servers:
        if s.name in seen:
            raise McpConfigError(f"duplicate mcp server name: {s.name!r}")
        seen.add(s.name)
    return McpConfig(
        servers=tuple(servers),
        failure_threshold=int(d.get("failure_threshold", 3)),
        call_timeout_s=int(d.get("call_timeout_s", 30)),
    )
