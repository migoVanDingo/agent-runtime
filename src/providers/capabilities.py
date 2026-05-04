"""Provider capability metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderCapabilities:
    tool_use: bool = True
    structured_json_schema: bool = False
    parallel_tool_calls: bool = False
    streaming: bool = False
    max_context_tokens: int | None = None
