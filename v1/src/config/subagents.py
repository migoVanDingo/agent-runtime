"""SubAgentsConfig — per-spec provider/model/limit overrides from config.yml.

Lets users pin a sub-agent to a specific provider/model without editing
code. E.g.::

    subagents:
      ghidra_analyst:
        provider: anthropic
        model: claude-opus-4-7
      code_writer:
        provider: openai
        model: gpt-5-codex

The runner merges these overrides into the registered SubAgentSpec at
dispatch time (see ``runtime.subagents.runner.SubAgentRunner._resolve_spec``).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SubAgentOverride:
    """Per-spec config overrides, merged at dispatch time."""
    provider: str | None = None
    model: str | None = None
    timeout_seconds: float | None = None
    max_iterations: int | None = None


@dataclass
class SubAgentsConfig:
    """Mapping of sub-agent name → optional override block.

    Missing keys: the registered spec's defaults are used. Unknown names
    in config: warning + ignored (we don't know what the user meant).
    """
    overrides: dict[str, SubAgentOverride] = field(default_factory=dict)

    def get(self, name: str) -> SubAgentOverride | None:
        return self.overrides.get(name)
