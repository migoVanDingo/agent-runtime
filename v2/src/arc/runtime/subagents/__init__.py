"""Sub-agent dispatch primitive — see _design/0020-subagent-dispatch.md.

A sub-agent is a scoped child AgentSession spawned as a tool call from a
parent session. It owns its own provider, model, system prompt, and tool
allowlist. The parent gets a structured result back; the child's transcript
stays out of the parent's context.

Public API for sub-agent authors is `arc.subagent_api` — this package is
the internal implementation.
"""
from __future__ import annotations

from arc.runtime.subagents.errors import (
    SubAgentError,
    SubAgentRecursionError,
    SubAgentTimeoutError,
)
from arc.runtime.subagents.guards import DispatchGuard, classify_error
from arc.runtime.subagents.registry import SubAgentBuildContext, SubAgentRegistry
from arc.runtime.subagents.result import SubAgentResult
from arc.runtime.subagents.runner import SubAgentRunner
from arc.runtime.subagents.spec import SubAgentSpec
from arc.runtime.subagents.tool_adapter import SubAgentTool
from arc.runtime.subagents.tripwire import inside_subagent, subagent_scope

__all__ = [
    "DispatchGuard",
    "SubAgentBuildContext",
    "SubAgentError",
    "SubAgentRecursionError",
    "SubAgentRegistry",
    "SubAgentResult",
    "SubAgentRunner",
    "SubAgentSpec",
    "SubAgentTimeoutError",
    "SubAgentTool",
    "classify_error",
    "inside_subagent",
    "subagent_scope",
]
