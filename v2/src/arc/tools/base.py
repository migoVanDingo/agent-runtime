"""Tool base class + tool registry.

A tool is a callable the agent can invoke during a turn. It declares an input
schema (JSON Schema) the LLM uses to construct calls, and exposes an `execute`
method the runtime invokes.

Tools are minimal on purpose. All policy (guards, escalation, paging) lives in
plugins per design §4.3 — the tool itself just executes and returns a string.

Per the "no-hardcoded-defaults" principle, every tool reads its configuration
via `from_config(cls, cfg: dict)` at registration time. The runtime passes
`config.tools.config.<tool_name>` to each tool's `from_config`. Tools should
never read os.environ or config.yml directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol


@dataclass(frozen=True)
class ToolInputSchema:
    """JSON Schema for a tool's input. Plain dict for transparency."""
    properties: dict[str, Any]
    required: list[str]

    def to_json_schema(self) -> dict[str, Any]:
        """The shape providers expect."""
        return {
            "type": "object",
            "properties": self.properties,
            "required": self.required,
        }


class Tool(Protocol):
    """Every tool implements this interface.

    Class attributes (set at class definition):
      name: short identifier — must match the config key under tools.config.*
      description: shown to the LLM; should be concise + action-oriented

    Methods:
      input_schema: returns ToolInputSchema describing accepted args
      execute: runs the tool, returns the string the model sees

    Construction:
      Tools that need config implement a `from_config(cls, cfg: dict)` classmethod
      that the registry calls during build. Tools that don't need config just
      use the default no-arg constructor.
    """

    name: ClassVar[str]
    description: ClassVar[str]

    @property
    def input_schema(self) -> ToolInputSchema: ...

    def execute(self, input: dict[str, Any]) -> str: ...


class ToolError(Exception):
    """Raise from a tool's execute() when something goes wrong.

    The runtime catches this and converts to a ToolResult with ok=False so
    the model sees the error and can recover or report it. Tools should NOT
    return error strings disguised as success — raise instead.
    """


class ToolRegistry:
    """Holds enabled tools, keyed by name. Built from config at startup."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"tool {name!r} not registered")
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
