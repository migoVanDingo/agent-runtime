"""Sub-agent spec + result dataclasses.

A ``SubAgentSpec`` is a declarative profile for a sub-agent type: which
toolsets, which skills, which provider, what the system prompt is, what
shape the response must take. Specs are registered ahead of time and
looked up by name when a tool or skill dispatches a sub-agent.

A ``SubAgentResult`` is what comes back. Always carries ``text``; carries
``structured`` only when the spec asked for JSON output. Carries cost +
timing for telemetry rollup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SubAgentSpec:
    """Profile for a sub-agent type. Registered ahead of time, used by tools/skills.

    Attributes:
        name: stable identifier (e.g. ``"ghidra_analyst"``). Used in
            telemetry scope tags (``subagent:ghidra_analyst``) and as the
            registry key.
        description: human-readable purpose, surfaced by introspection
            commands and in plugin manifests.
        provider: LLM provider override (e.g. ``"anthropic"``). ``None``
            means inherit the parent's provider.
        model: LLM model override (e.g. ``"claude-opus-4-7"``). ``None``
            means inherit.
        toolset_names: toolsets exposed to the child. Empty tuple means
            "no tools" (pure-reasoning sub-agents). The runner ALWAYS
            filters out any SubAgentTool from these toolsets to enforce
            the no-recursion rule.
        skill_names: skill registry subset exposed to the child. Empty
            means no skills.
        system_prompt: specialised system prompt that overrides the
            parent's. Empty string falls back to the default agent system
            prompt — but most sub-agents will want their own.
        response_format: ``"text"`` (default) or ``"json"``. When ``"json"``,
            the child is instructed to return JSON conforming to
            ``response_schema``, and the runner parses it into
            ``SubAgentResult.structured``.
        response_schema: required when ``response_format == "json"``. JSON
            schema dict the child's response must conform to.
        timeout_seconds: wall-clock cap on the entire child execution.
            Hitting it raises ``SubAgentTimeoutError`` in the runner.
        max_iterations: cap on the child's tool-loop iterations. Mirrors
            the parent's ``runtime.pipeline.max_iterations``.
    """
    name: str
    description: str
    provider: str | None = None
    model: str | None = None
    toolset_names: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    system_prompt: str = ""
    response_format: str = "text"
    response_schema: dict[str, Any] | None = None
    timeout_seconds: float = 300.0
    max_iterations: int = 20

    def __post_init__(self) -> None:
        if self.response_format == "json" and self.response_schema is None:
            raise ValueError(
                f"SubAgentSpec {self.name!r}: response_format='json' requires response_schema"
            )
        if self.response_format not in ("text", "json"):
            raise ValueError(
                f"SubAgentSpec {self.name!r}: response_format must be 'text' or 'json', "
                f"got {self.response_format!r}"
            )


@dataclass
class SubAgentResult:
    """Outcome of a single sub-agent run."""
    ok: bool
    text: str
    structured: dict[str, Any] | None = None
    elapsed_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    error: str | None = None


class SubAgentError(Exception):
    """Base class for sub-agent errors."""


class SubAgentTimeoutError(SubAgentError):
    """Raised when a sub-agent exceeds ``SubAgentSpec.timeout_seconds``."""


class SubAgentRecursionError(SubAgentError):
    """Raised when code attempts to spawn a sub-agent from within a sub-agent.

    v1 hard-prohibits recursion (see _plans/0090 §6 0090c). Lift only with
    explicit depth limit + budget propagation in a future plan (0094).
    """
