"""Adapt a discovered MCP tool into an arc Tool.

The adapted tool is an ordinary arc Tool (duck-typed: `name`, `description`,
`input_schema`, `execute`), so it merges into the registry and inherits the
guard/safety gates, the generic `tool.call.*` events, and replay. MCP-specific
events are emitted by the manager around the call.
"""
from __future__ import annotations

import re
from typing import Any

from arc.mcp.manager import McpCallError, McpManager, ToolDef
from arc.tools.base import ToolError, ToolInputSchema

_NAME_OK = re.compile(r"[^a-zA-Z0-9_-]")


def _tool_name(prefix: str, name: str) -> str:
    """Provider-safe tool name: `{prefix}_{name}`, sanitized, capped at 64."""
    raw = f"{prefix}_{name}" if prefix else name
    return _NAME_OK.sub("_", raw)[:64]


class McpTool:
    def __init__(self, tooldef: ToolDef, manager: McpManager, prefix: str) -> None:
        self.name = _tool_name(prefix, tooldef.name)
        self.description = tooldef.description or f"MCP tool {tooldef.name} (server {tooldef.server})"
        self._server = tooldef.server
        self._mcp_name = tooldef.name
        self._manager = manager
        schema = tooldef.input_schema if isinstance(tooldef.input_schema, dict) else {}
        props = schema.get("properties")
        self._schema = ToolInputSchema(
            properties=props if isinstance(props, dict) else {},
            required=list(schema.get("required") or []),
        )

    @property
    def input_schema(self) -> ToolInputSchema:
        return self._schema

    def execute(self, input: dict[str, Any]) -> str:
        try:
            out = self._manager.call_tool(self._server, self._mcp_name, input)
        except McpCallError as exc:
            raise ToolError(str(exc)) from exc
        if out["is_error"]:
            raise ToolError(out["text"] or f"MCP tool {self._mcp_name} returned an error")
        return out["text"]
