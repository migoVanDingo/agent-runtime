"""Structured tool execution result.

Existing stages still pass string content to the LLM. This type gives the
runtime a stable internal shape for future policy, event, and replay work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str
    error_code: str | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def success(cls, content: str, **metadata: Any) -> "ToolResult":
        return cls(ok=True, content=content, metadata=metadata or None)

    @classmethod
    def error(cls, content: str, error_code: str = "tool_error", **metadata: Any) -> "ToolResult":
        return cls(ok=False, content=content, error_code=error_code, metadata=metadata or None)

    def to_llm_content(self) -> str:
        return self.content
