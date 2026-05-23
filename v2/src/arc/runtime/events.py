"""Event schema.

The shape and semantics are in _design/0001-foundation-phase0-design.md §6.
Events are immutable, ordered, identified, and self-contained — given an
event log, replay reconstructs the entire session.

Critical requirement (§6.3): `content` must be canonical-byte-faithful to
what was sent/received on the wire. No pretty-printing, no key reordering.
The recorder serializes events with sort_keys=False precisely for this reason.

The catalog of event types here is the INITIAL set from §6.2. New types may
be added as features land (they're just strings).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from arc.runtime.ids import new_event_id
from arc.runtime.scope import (
    current_parent_event_id,
    current_scope,
    current_session_id,
    current_turn_id,
)

# Current schema version. Bump on any breaking change to the envelope.
SCHEMA_VERSION = 1


# ── Event type catalog ──────────────────────────────────────────────────────
# String constants so the codebase has one canonical spelling per event type.
# Add new types here, grep finds every usage.

class EventType:
    # Lifecycle
    SESSION_STARTED = "session.started"
    SESSION_ENDED = "session.ended"
    TURN_STARTED = "turn.started"
    TURN_ENDED = "turn.ended"

    # LLM boundary
    LLM_CALL_STARTED = "llm.call.started"
    LLM_CALL_COMPLETED = "llm.call.completed"
    LLM_CALL_FAILED = "llm.call.failed"

    # Tool boundary
    TOOL_CALL_STARTED = "tool.call.started"
    TOOL_CALL_COMPLETED = "tool.call.completed"
    TOOL_CALL_FAILED = "tool.call.failed"
    TOOL_CALL_DENIED = "tool.call.denied"

    # Hooks & plugins
    HOOK_FIRED = "hook.fired"
    PLUGIN_HOOK_FAILED = "plugin.hook.failed"
    PLUGIN_DISABLED = "plugin.disabled"

    # Control flow
    PAUSE_CHECKPOINT_PASSED = "pause.checkpoint.passed"
    PAUSE_REQUESTED = "pause.requested"
    PAUSE_RESUMED = "pause.resumed"

    # Cycle: N identical tool calls in a row → loop forces wrap-up
    RUNTIME_CYCLE_DETECTED = "runtime.cycle_detected"

    # Context manager packed the message list (dropped fragments to fit budget)
    RUNTIME_CONTEXT_PACKED = "runtime.context_packed"

    # User typed /clear in the TUI; conversation reset in place mid-session
    CONVERSATION_CLEARED = "runtime.conversation_cleared"

    # Destructive-action gate (0012). Pattern matched a destructive command;
    # plugin asked the user via UserGate. Three terminal states:
    #   requested → allowed (scope=once|session|remembered)
    #   requested → denied
    SAFETY_CONFIRMATION_REQUESTED = "safety.confirmation.requested"
    SAFETY_CONFIRMATION_ALLOWED = "safety.confirmation.allowed"
    SAFETY_CONFIRMATION_DENIED = "safety.confirmation.denied"

    # Cross-provider replay (0019)
    #   SESSION_ABORTED — session ended abnormally (cost cap, user cancel, fatal provider error).
    #                     Emitted BEFORE SESSION_ENDED so the final event still closes the session.
    #   REPLAY_TARGET_COMPLETED — batch driver per-target outcome summary
    #                             (source session id, target session id, provider/model, cost, wallclock).
    SESSION_ABORTED = "session.aborted"
    REPLAY_TARGET_COMPLETED = "replay.target_completed"

    # Catch-all for non-categorized observations
    EVENT_EMITTED = "event.emitted"


# Severity levels — used by sinks for filtering
class Severity:
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass
class RuntimeEvent:
    """One observable moment. Immutable from the consumer's perspective —
    once `emit()` is called, no one should mutate the instance.

    Identity (session/turn/scope/parent) is auto-filled from contextvars at
    construction time. Pass `parent_event_id=` explicitly to override.

    Both `payload` (small, searchable, indexed) and `content` (large, may be
    paged out for storage savings) are dicts — the recorder serializes them
    verbatim. To preserve byte-fidelity, callers should put raw provider/tool
    bytes into `content` without normalization.
    """
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    content: dict[str, Any] = field(default_factory=dict)
    stage: str = "core"
    severity: str = Severity.INFO
    duration_ms: int | None = None

    # Auto-filled from contextvars (caller can override)
    event_id: str = field(default_factory=new_event_id)
    session_id: str | None = field(default_factory=current_session_id)
    turn_id: str | None = field(default_factory=current_turn_id)
    scope: str = field(default_factory=current_scope)
    parent_event_id: str | None = field(default_factory=current_parent_event_id)

    # Timestamps — both wall and monotonic. Wall for humans, monotonic for ordering
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="microseconds"))
    ts_monotonic_ns: int = field(default_factory=time.monotonic_ns)

    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Canonical dict representation for serialization.

        Field ordering matches the envelope spec in §6.1 — preserved here as the
        explicit construction order so JSON output is predictable.
        """
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "scope": self.scope,
            "parent_event_id": self.parent_event_id,
            "ts": self.ts,
            "ts_monotonic_ns": self.ts_monotonic_ns,
            "type": self.type,
            "stage": self.stage,
            "severity": self.severity,
            "duration_ms": self.duration_ms,
            "payload": self.payload,
            "content": self.content,
            "schema_version": self.schema_version,
        }
