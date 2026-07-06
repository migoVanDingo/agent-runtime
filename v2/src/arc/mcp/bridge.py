"""The built-in `mcp` plugin: bridges configured MCP servers into the registry.

Session-scoped: connects on `on_session_start`, contributes the adapted tools
via `provides_tools()`, disconnects on `on_session_end`. Never raises on a
server failure — the manager isolates per-server and emits events; the session
keeps running (possibly with fewer/zero MCP tools).
"""
from __future__ import annotations

from typing import Any

from arc.mcp.adapter import McpTool
from arc.mcp.config import McpConfig
from arc.mcp.manager import McpManager
from arc.runtime.events import EventType, RuntimeEvent, Severity


class McpBridge:
    name = "mcp"

    def __init__(self, cfg: McpConfig) -> None:
        self._cfg = cfg
        self._bus: Any = None
        self._manager: McpManager | None = None
        self._tools: list[Any] = []

    def bind_bus(self, bus: Any) -> None:
        self._bus = bus

    def on_session_start(self, ctx: Any) -> None:
        self._manager = McpManager(self._cfg, bus=self._bus)
        self._manager.connect_all()
        prefixes = {s.name: s.prefix for s in self._cfg.servers}
        # Drop adapted-name collisions here (two server tools sanitizing to the
        # same arc name) — otherwise merge_plugin_tools raises uncaught and
        # crashes session startup, defeating the per-server isolation.
        tools: list[Any] = []
        seen: set[str] = set()
        for td in self._manager.all_tools():
            tool = McpTool(td, self._manager, prefixes.get(td.server, td.server))
            if tool.name in seen:
                if self._bus is not None:
                    self._bus.emit(RuntimeEvent(
                        type=EventType.MCP_SERVER_ERROR, stage="mcp",
                        severity=Severity.WARN,
                        payload={"server": td.server, "tool": td.name,
                                 "adapted_name": tool.name,
                                 "reason": "adapted tool name collides; dropped"},
                    ))
                continue
            seen.add(tool.name)
            tools.append(tool)
        self._tools = tools

    def on_session_end(self, ctx: Any, outcome: Any = None) -> None:
        if self._manager is not None:
            self._manager.disconnect_all()
            self._manager = None
        self._tools = []

    def provides_tools(self) -> list[Any]:
        return list(self._tools)

    # Introspection for the CLI / setup section without opening a session.
    def status(self) -> list[dict]:
        if self._manager is not None:
            return self._manager.status()
        return [
            {"name": s.name, "transport": s.transport, "enabled": s.enabled,
             "state": "disconnected", "tool_count": 0, "error": ""}
            for s in self._cfg.servers
        ]
