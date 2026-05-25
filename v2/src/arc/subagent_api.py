"""Public sub-agent API — the one stable import path for out-of-tree sub-agents.

External sub-agent packages (`pip install arc-sub-agent-*`) should import
from here:

    from arc.subagent_api import (
        SubAgentSpec,
        SubAgentBuildContext,
        SubAgentResult,
        SubAgentError,
        SubAgentTimeoutError,
        SubAgentRecursionError,
    )

This module is a re-export shim, parallel to `arc.plugin_api`. We can
refactor `arc.runtime.subagents` freely as long as this shim keeps
re-exporting the right names.

When the surface needs to evolve (rename, deprecate, add a kwarg), bump
__api_version__ and document the change. See docs/SUBAGENT_API.md in the
template repo for the breakage policy.

Importing from `arc.runtime.subagents` directly is NOT supported for
out-of-tree packages — that module can move without notice.
"""
from __future__ import annotations

# API version. Bump per the breakage policy:
#   - patch / minor: additive (new symbols, new optional fields on Spec)
#   - major:         breaking (rename, removed field, signature change)
# Sub-agent packages can assert >= (X, Y) to gate on feature availability.
#
# 0.2 (2026-05-24) — added SubAgentSpec.params dict field for provider-
#                    specific config (vertex_gemini's project_id + region,
#                    future providers' equivalents). Additive — existing
#                    0.1 specs still work (default factory = empty dict).
__api_version__: tuple[int, int] = (0, 2)


from arc.runtime.subagents.errors import (
    SubAgentError,
    SubAgentRecursionError,
    SubAgentTimeoutError,
)
from arc.runtime.subagents.registry import SubAgentBuildContext
from arc.runtime.subagents.result import SubAgentResult
from arc.runtime.subagents.spec import SubAgentSpec


__all__ = [
    "__api_version__",
    "SubAgentBuildContext",
    "SubAgentError",
    "SubAgentRecursionError",
    "SubAgentResult",
    "SubAgentSpec",
    "SubAgentTimeoutError",
]
