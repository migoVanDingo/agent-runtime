"""RuntimeEvent → AgentEvent translation.

Single seam between the agent's internal event vocabulary and the
AgentService contract. Update this function — not the UI — when internal
bus event names change.

All translation is best-effort: missing payload fields silently default.
Unknown event types return None and are not surfaced.
"""
from __future__ import annotations

import json
from typing import Any

from runtime.events.schema import RuntimeEvent
from service.events import (
    AgentEvent,
    SessionStarted, SessionEnded,
    TurnStarted,
    StageStarted, StageCompleted,
    ToolCallStarted, ToolCallCompleted,
)


def translate(event: RuntimeEvent, session_id: str) -> AgentEvent | None:
    """Map a RuntimeEvent to a typed AgentEvent, or None to suppress.

    Args:
        event: The raw bus event from the agent runtime.
        session_id: Current session ID (not on RuntimeEvent directly).

    Returns:
        A typed AgentEvent for the UI, or None if this event is not surfaced.

    NOTE: turn.completed and turn.failed are suppressed here because the
    service driver synthesizes them directly with richer context (elapsed_ms,
    full response text). Receiving them from the bus would produce duplicates.
    """
    p: dict[str, Any] = event.payload or {}
    turn_id: str | None = getattr(event.identity, "turn_id", None)
    kwargs: dict[str, Any] = dict(session_id=session_id, turn_id=turn_id)

    t = event.event_type

    if t == "session.started":
        return SessionStarted(**kwargs, resumed=False)
    if t == "session.resumed":
        return SessionStarted(**kwargs, resumed=True)
    if t == "session.ended":
        return SessionEnded(**kwargs)
    if t == "turn.started":
        return TurnStarted(**kwargs, message_preview=str(p.get("message_preview", ""))[:300])

    # turn.completed / turn.failed suppressed — emitted by the service driver.
    if t in ("turn.completed", "turn.failed"):
        return None

    if t == "stage.started":
        return StageStarted(**kwargs, stage=str(p.get("stage_name", event.stage or "")))
    if t == "stage.finished":
        return StageCompleted(
            **kwargs,
            stage=str(p.get("stage_name", event.stage or "")),
            status=str(p.get("status", "ok")),
            duration_ms=int(p.get("duration_ms", 0)),
        )

    if t == "tool.call.started":
        args_raw = p.get("input_preview", "")
        return ToolCallStarted(
            **kwargs,
            tool_name=str(p.get("tool_name", "")),
            tool_call_id=str(p.get("tool_call_id", getattr(event.identity, "tool_call_id", ""))),
            args_preview=str(args_raw)[:200],
        )
    if t == "tool.call.completed":
        return ToolCallCompleted(
            **kwargs,
            tool_name=str(p.get("tool_name", "")),
            tool_call_id=str(p.get("tool_call_id", getattr(event.identity, "tool_call_id", ""))),
            result_preview=str(p.get("result_preview", ""))[:200],
            error="" if p.get("ok", True) else str(p.get("error_code", "error")),
        )

    # All other event types (escalation.*, policy.decision, etc.)
    # are not surfaced to the UI in this version.
    return None
