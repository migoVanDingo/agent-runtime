"""SubAgentSpec — the declarative shape of a sub-agent.

A spec is pure data. The runner reads it, builds a child config, spawns
the child AgentSession. Authors construct one in their entry-point `build()`
and register it via the `arc.subagents` entry-point group.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal


SpecSource = Literal["builtin", "plugin", "config"]


@dataclass(frozen=True)
class SubAgentSpec:
    """Frozen declarative description of a sub-agent."""

    # Registry key, also used as the parent's tool name (`subagent_<name>`).
    # Lowercase + underscores conventionally. The runner does not normalize.
    name: str

    # Shown to the parent agent in the tool schema — make it actionable
    # (what to pass in the task string).
    description: str

    # Provider key matching arc.providers.build (`anthropic`, `gemini`,
    # `ollama`, `llama_cpp`). The runner constructs a fresh provider.
    provider: str

    # Provider-specific model id.
    model: str

    # The child's system prompt. May be long — load from an external file
    # in `build()` rather than embedding multi-line strings.
    system_prompt: str

    # Tool names the child gets access to. Intersected with the parent's
    # tool registry at dispatch time. Empty tuple means the child has no
    # tools (chat-only).
    tools: tuple[str, ...] = ()

    # Per-dispatch wall-clock timeout. The child runs in a watchdog
    # thread; on hit, it's cancelled at the next turn boundary.
    timeout_s: float = 300.0

    # Cap on the child's ReAct loop iterations.
    max_turns: int = 25

    # Provider override fields. None means inherit from arc's catalog
    # (the same env-var and base-url the user already has configured).
    api_key_env: str | None = None
    base_url: str | None = None

    # JSON-shape sketch appended to the child's system prompt so it knows
    # what shape its final message should take. Not enforced — the parent
    # is responsible for parsing.
    expected_output: str | None = None

    # Dispatch guards (per parent session, per spec). See guards.py.
    max_dispatches_per_session: int = 5
    max_consecutive_failures: int = 2
    max_transient_retries: int = 2

    # Provider-specific config to thread into the child's ProviderConfig.params.
    # Used by sub-agents that pin a provider needing extra config beyond
    # api_key_env / base_url (e.g., `vertex_gemini` needs project_id + region).
    # Frozen dict-equivalent — pass a regular dict at construction; the spec
    # stays equal-by-value across instances with same contents.
    params: dict[str, Any] = field(default_factory=dict)

    # Provenance — set by the registry during discovery/merge. Authors
    # don't need to set these; the registry overrides.
    source: SpecSource = "plugin"
    source_package: str | None = None

    def merged_with(self, overrides: dict) -> "SubAgentSpec":
        """Return a copy with the listed fields overridden.

        Used by the registry to apply config-block overrides on top of the
        baseline spec returned by `build()`. Unknown keys raise so typos
        surface loudly instead of being silently ignored.
        """
        if not overrides:
            return self
        valid = {f.name for f in self.__dataclass_fields__.values()}
        unknown = set(overrides) - valid
        if unknown:
            raise ValueError(
                f"sub-agent {self.name!r}: unknown override fields: {sorted(unknown)}\n"
                f"  valid: {sorted(valid - {'source', 'source_package'})}"
            )
        applied = dict(overrides)
        # tools must be a tuple post-merge for hashability/equality.
        if "tools" in applied and not isinstance(applied["tools"], tuple):
            applied["tools"] = tuple(applied["tools"])
        return replace(self, **applied)
