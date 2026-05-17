"""Sub-agent dispatch — scoped child agents for context-heavy tasks.

See ``_plans/0090-context-discipline-and-subagents.md`` §3 prong B.

The runtime-as-god alignment:
- Parent owns sub-agent lifecycle (spawn, kill, timeout).
- Child is a passive executor that runs its scoped task and returns.
- Escalations propagate through the parent's user_gate (same UI surface).
- Recursion is hard-prohibited in v1 (two layers: registry filter +
  contextvar tripwire).

Public API:
- ``SubAgentSpec`` — declarative profile (toolsets, provider, prompt, schema).
- ``SubAgentResult`` — outcome with text + optional structured + cost.
- ``SubAgentRunner`` — invokes a child Agent for one task.
- ``register_spec`` / ``get_spec`` / ``known_specs`` — process-level registry.
- ``parent_context`` — contextvars threading parent state into tool dispatch.
"""
from runtime.subagents.context import (
    current_parent_agent,
    current_parent_turn_id,
    current_pause_check,
    parent_context,
)
from runtime.subagents.registry import (
    all_specs,
    clear_for_tests,
    get_spec,
    known_specs,
    register_spec,
)
from runtime.subagents.runner import SubAgentRunner
from runtime.subagents.spec import (
    SubAgentError,
    SubAgentRecursionError,
    SubAgentResult,
    SubAgentSpec,
    SubAgentTimeoutError,
)

__all__ = [
    "SubAgentSpec",
    "SubAgentResult",
    "SubAgentRunner",
    "SubAgentError",
    "SubAgentRecursionError",
    "SubAgentTimeoutError",
    "register_spec",
    "get_spec",
    "known_specs",
    "all_specs",
    "clear_for_tests",
    "parent_context",
    "current_parent_agent",
    "current_pause_check",
    "current_parent_turn_id",
]
