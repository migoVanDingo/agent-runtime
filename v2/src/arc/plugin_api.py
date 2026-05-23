"""Public plugin API — the one stable import path for out-of-tree plugins.

External plugins (`pip install arc-plugin-*`) should import from here:

    from arc.plugin_api import (
        Tool, ToolInputSchema, ToolError,
        PluginBuildContext, SessionContext, TurnOutcome,
        RuntimeEvent, EventType,
    )

This module is a *re-export shim*: every symbol exposed here lives in some
internal module under `arc/`. The whole point of the shim is to give plugin
authors a frozen surface — we can refactor `arc.runtime.hooks` / `arc.tools.base`
freely as long as `arc.plugin_api` keeps re-exporting the right names.

When the surface needs to evolve (rename, deprecate, add a kwarg), bump
__api_version__ and document the change. See docs/PLUGIN_API.md in the
template repo for the breakage policy.

Importing from `arc.tools.base`, `arc.runtime.hooks`, etc. directly is NOT
supported for plugins — those modules can move without notice.
"""
from __future__ import annotations

# API version. Bump per the breakage policy:
#   - patch / minor: additive (new symbols, new optional methods)
#   - major:         breaking (rename, removed symbol, signature change)
# Plugins can assert >= (X, Y) to gate on feature availability.
__api_version__: tuple[int, int] = (0, 1)


# ── Tool surface ──────────────────────────────────────────────────────────
from arc.tools.base import (
    Tool,
    ToolError,
    ToolInputSchema,
    ToolRegistry,
)


# ── Plugin lifecycle / hook payloads ──────────────────────────────────────
from arc.runtime.hooks import (
    # Sentinel for "no change"
    PASS_THROUGH,
    # Hook payloads (passed to / returned from hook methods)
    ContentBlock,
    LLMRequest,
    LLMResponse,
    Message,
    SessionContext,
    Step,
    StepAssessment,
    ToolCall,
    ToolDenial,
    ToolResult,
    ToolSpec,
    TurnContext,
    TurnOutcome,
    UserInput,
    # Control-flow exceptions raised from pause_check
    Cancelled,
    PauseRequested,
)


# ── Events ────────────────────────────────────────────────────────────────
from arc.runtime.events import (
    EventType,
    RuntimeEvent,
    Severity,
)


# ── Plugin construction context ───────────────────────────────────────────
# PluginBuildContext lives in arc.plugins.__init__ today. Re-export it here
# so plugins don't need to import from a private module. (If we ever move it,
# the shim updates and plugins don't break.)
from arc.plugins import PluginBuildContext


__all__ = [
    # Version
    "__api_version__",
    # Tools
    "Tool",
    "ToolError",
    "ToolInputSchema",
    "ToolRegistry",
    # Hook payloads
    "PASS_THROUGH",
    "ContentBlock",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "SessionContext",
    "Step",
    "StepAssessment",
    "ToolCall",
    "ToolDenial",
    "ToolResult",
    "ToolSpec",
    "TurnContext",
    "TurnOutcome",
    "UserInput",
    "Cancelled",
    "PauseRequested",
    # Events
    "EventType",
    "RuntimeEvent",
    "Severity",
    # Construction
    "PluginBuildContext",
]
