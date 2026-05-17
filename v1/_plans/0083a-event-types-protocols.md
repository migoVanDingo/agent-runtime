# 0083a — Event types + Protocols

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §3.1 and §3.2.
> This phase has no dependencies. Land it before any other 0083 phase.

## Goal

Define all typed `AgentEvent` dataclasses (five families, ~14 event types) and
the `AgentService` / `TurnHandle` `Protocol` classes. No implementations.
These types are the contract that every later phase codes against — get them
right here so no phase has to change them.

## Files to create

| File | Purpose |
|------|---------|
| `src/service/__init__.py` | Package marker; re-exports `AgentService`, `TurnHandle`, `AgentEvent` |
| `src/service/events.py` | All `AgentEvent` dataclasses + `AgentEvent` union type |
| `src/service/interface.py` | `AgentService` and `TurnHandle` Protocol definitions |

No existing files are modified in this phase.

## Detailed implementation

### `src/service/events.py`

All event dataclasses share a common base with `type`, `timestamp`,
`session_id`, and optional `turn_id`. Use `@dataclass` (not frozen) so
fields can be set after construction in serialization helpers.

```python
"""Service-layer event taxonomy for the AgentService contract.

All events emitted by AgentService implementations. Discriminated by the
`type` field string — stable across versions, safe for JSON transport.

Families:
  Session  — session lifecycle
  Turn     — turn lifecycle (one user message → one agent response)
  Stage    — pipeline stage progress (replaces spinner)
  Content  — streaming token chunks and final message
  Tool     — tool call lifecycle (collapsible cards in TUI)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Union


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Base ──────────────────────────────────────────────────────────────────────

@dataclass
class _EventBase:
    """Common fields on every AgentEvent.

    `type` must be set as a ClassVar default in every subclass so that
    from_dict() can dispatch on it without inspecting class names.
    """
    type: str
    session_id: str
    timestamp: str = field(default_factory=_now_iso)
    turn_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "_EventBase":
        """Reconstruct from a dict produced by to_dict(). Dispatches by type."""
        return _EVENT_REGISTRY[d["type"]](**{
            k: v for k, v in d.items()
            if k in cls.__dataclass_fields__  # type: ignore[attr-defined]
        })


# ── Session family ─────────────────────────────────────────────────────────────

@dataclass
class SessionStarted(_EventBase):
    type: str = field(default="session.started", init=False)
    resumed: bool = False
    session_dir: str = ""


@dataclass
class SessionEnded(_EventBase):
    type: str = field(default="session.ended", init=False)
    reason: str = "normal"   # "normal" | "error" | "cancelled"


# ── Turn family ───────────────────────────────────────────────────────────────

@dataclass
class TurnStarted(_EventBase):
    type: str = field(default="turn.started", init=False)
    message_preview: str = ""


@dataclass
class TurnCompleted(_EventBase):
    type: str = field(default="turn.completed", init=False)
    response_preview: str = ""
    elapsed_ms: int = 0


@dataclass
class TurnFailed(_EventBase):
    type: str = field(default="turn.failed", init=False)
    error: str = ""


@dataclass
class TurnCancelled(_EventBase):
    type: str = field(default="turn.cancelled", init=False)
    at_stage: str = ""   # which stage/checkpoint fired the cancel


# ── Stage family ──────────────────────────────────────────────────────────────

@dataclass
class StageStarted(_EventBase):
    type: str = field(default="stage.started", init=False)
    stage: str = ""
    message: str = ""


@dataclass
class StageProgress(_EventBase):
    type: str = field(default="stage.progress", init=False)
    stage: str = ""
    message: str = ""


@dataclass
class StageCompleted(_EventBase):
    type: str = field(default="stage.completed", init=False)
    stage: str = ""
    status: str = "ok"       # "ok" | "abort" | "done"
    duration_ms: int = 0


# ── Content family ────────────────────────────────────────────────────────────

@dataclass
class TokenChunk(_EventBase):
    """A streamed token chunk. May arrive hundreds of times per turn.

    This is the only high-frequency event; the BoundedDropQueue may drop
    oldest TokenChunks on overflow. The full text arrives in MessageComplete.
    """
    type: str = field(default="content.token_chunk", init=False)
    text: str = ""


@dataclass
class MessageComplete(_EventBase):
    """Final assembled text of an agent response.

    Always emitted after all TokenChunks for a turn. The TUI uses this to
    swap the streaming plain-text bubble for a rendered Markdown widget.
    """
    type: str = field(default="content.message_complete", init=False)
    text: str = ""


# ── Tool family ───────────────────────────────────────────────────────────────

@dataclass
class ToolCallStarted(_EventBase):
    type: str = field(default="tool.call.started", init=False)
    tool_name: str = ""
    tool_call_id: str = ""
    args_preview: str = ""   # first 200 chars of serialized args


@dataclass
class ToolCallCompleted(_EventBase):
    type: str = field(default="tool.call.completed", init=False)
    tool_name: str = ""
    tool_call_id: str = ""
    result_preview: str = ""  # first 200 chars of result
    error: str = ""           # non-empty if tool raised


# ── Discriminated union ───────────────────────────────────────────────────────

AgentEvent = Union[
    SessionStarted, SessionEnded,
    TurnStarted, TurnCompleted, TurnFailed, TurnCancelled,
    StageStarted, StageProgress, StageCompleted,
    TokenChunk, MessageComplete,
    ToolCallStarted, ToolCallCompleted,
]

# Registry for from_dict dispatch — one entry per concrete type.
_EVENT_REGISTRY: dict[str, type] = {
    "session.started":          SessionStarted,
    "session.ended":            SessionEnded,
    "turn.started":             TurnStarted,
    "turn.completed":           TurnCompleted,
    "turn.failed":              TurnFailed,
    "turn.cancelled":           TurnCancelled,
    "stage.started":            StageStarted,
    "stage.progress":           StageProgress,
    "stage.completed":          StageCompleted,
    "content.token_chunk":      TokenChunk,
    "content.message_complete": MessageComplete,
    "tool.call.started":        ToolCallStarted,
    "tool.call.completed":      ToolCallCompleted,
}


def event_from_dict(d: dict[str, Any]) -> AgentEvent:
    """Reconstruct a typed AgentEvent from a dict (e.g., from JSON transport)."""
    cls = _EVENT_REGISTRY.get(d.get("type", ""))
    if cls is None:
        raise ValueError(f"Unknown AgentEvent type: {d.get('type')!r}")
    # Pass only keys that exist on the dataclass to tolerate forward-compat extra fields.
    known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return cls(**{k: v for k, v in d.items() if k in known})


def event_to_json(event: AgentEvent) -> str:
    return json.dumps(asdict(event), default=str)


def event_from_json(s: str) -> AgentEvent:
    return event_from_dict(json.loads(s))
```

**Implementation note on `type` field:** Using `field(default=..., init=False)`
means `type` is excluded from `__init__` and always holds the string constant.
This is the standard pattern for discriminated-union dataclasses in Python.
`asdict()` includes it normally so `to_dict()` / JSON round-trips work.

### `src/service/interface.py`

```python
"""AgentService and TurnHandle Protocol definitions.

These are the contracts every frontend codes against. No concrete
implementations live here — only the shape of the interface.

Protocol (not ABC) means duck-typing works: an HttpAgentService written
later needs no inheritance from these classes.
"""
from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from service.events import AgentEvent


@runtime_checkable
class TurnHandle(Protocol):
    """Handle to a single in-flight turn.

    Obtained from AgentService.send(). Provides:
    - A filtered event stream (only events for this turn)
    - A blocking wait() for the final response text
    - One-shot cancel for this turn specifically

    The service itself remains available after a turn ends or is cancelled.
    """

    @property
    def turn_id(self) -> str:
        """Stable identifier for this turn, matching AgentEvent.turn_id."""
        ...

    def events(self) -> AsyncIterator[AgentEvent]:
        """Async generator of events scoped to this turn.

        Yields from the same underlying queue as AgentService.events() but
        filters to events where event.turn_id == self.turn_id. Exits when
        TurnCompleted, TurnFailed, or TurnCancelled is received.
        """
        ...

    async def wait(self) -> str:
        """Block until the turn completes. Returns the full response text.

        Raises TurnCancelledError if the turn is cancelled before completion.
        Raises TurnFailedError if the agent raises an unhandled exception.
        """
        ...

    async def cancel(self) -> None:
        """Request cancellation of this specific turn."""
        ...


@runtime_checkable
class AgentService(Protocol):
    """The boundary between any frontend and the agent runtime.

    All methods are async. send() is the primary entry point — it starts
    a turn and returns a TurnHandle immediately; the caller consumes events
    from either events() (global) or handle.events() (turn-scoped).

    Implementations must be non-reentrant on send(): if is_busy is True,
    calling send() again must raise RuntimeError. The TUI queues messages
    at its layer instead.
    """

    @property
    def session_id(self) -> str:
        """Stable identifier for the current session."""
        ...

    @property
    def is_busy(self) -> bool:
        """True while a turn is in flight. Read by the TUI to show/hide the queue badge."""
        ...

    async def send(self, message: str) -> TurnHandle:
        """Start a new turn with `message` as the user input.

        Returns immediately with a TurnHandle. The turn runs concurrently.
        Raises RuntimeError if is_busy is True (caller must queue).
        """
        ...

    def events(self) -> AsyncIterator[AgentEvent]:
        """Global event stream for the lifetime of this service.

        Yields every AgentEvent emitted across all turns and lifecycle
        transitions. The UI's main event dispatcher subscribes here once
        on startup. Never raises; exits when close() is called.
        """
        ...

    async def pause(self) -> None:
        """Request that the running turn pause at the next checkpoint.

        Returns immediately; the turn may not pause for up to one tool-call
        or stage transition. After pause(), is_busy remains True.
        """
        ...

    async def resume(self) -> None:
        """Resume a paused turn."""
        ...

    async def cancel_current_turn(self) -> None:
        """Cancel the in-flight turn. Emits TurnCancelled when effective."""
        ...

    def conversation_history(self) -> list[dict]:
        """Return the raw conversation history (list of message dicts)."""
        ...

    async def close(self) -> None:
        """Shut down the service cleanly. Cancels any in-flight turn first."""
        ...
```

### `src/service/__init__.py`

```python
"""Service layer — the contract between any frontend and the agent runtime.

Public surface:
  AgentService    — Protocol for any service implementation
  TurnHandle      — Protocol for a single in-flight turn
  AgentEvent      — Union type of all typed events
  event_from_dict — Reconstruct an AgentEvent from a dict (for transport)
  event_to_json   — Serialize an AgentEvent to JSON string
  event_from_json — Deserialize an AgentEvent from JSON string
"""
from service.events import (
    AgentEvent,
    SessionStarted, SessionEnded,
    TurnStarted, TurnCompleted, TurnFailed, TurnCancelled,
    StageStarted, StageProgress, StageCompleted,
    TokenChunk, MessageComplete,
    ToolCallStarted, ToolCallCompleted,
    event_from_dict, event_to_json, event_from_json,
)
from service.interface import AgentService, TurnHandle

__all__ = [
    "AgentService", "TurnHandle", "AgentEvent",
    "SessionStarted", "SessionEnded",
    "TurnStarted", "TurnCompleted", "TurnFailed", "TurnCancelled",
    "StageStarted", "StageProgress", "StageCompleted",
    "TokenChunk", "MessageComplete",
    "ToolCallStarted", "ToolCallCompleted",
    "event_from_dict", "event_to_json", "event_from_json",
]
```

## Error types to define

Add these to `src/service/errors.py` (new file):

```python
"""Exceptions raised across the service boundary."""


class TurnCancelledError(Exception):
    """Raised by TurnHandle.wait() when a turn is cancelled before completion."""
    def __init__(self, at_stage: str = "") -> None:
        self.at_stage = at_stage
        super().__init__(f"Turn cancelled at: {at_stage}" if at_stage else "Turn cancelled")


class TurnFailedError(Exception):
    """Raised by TurnHandle.wait() when the agent raises an unhandled exception."""
    def __init__(self, message: str = "") -> None:
        super().__init__(message)
```

Also export these from `src/service/__init__.py`.

## Verification

```bash
# 1. Module imports without error
cd /Users/bubz/Developer/agent/runtime/agent-runtime
python -c "from service import AgentService, TurnHandle, AgentEvent; print('ok')"

# 2. Round-trip serialization for every event type
python - <<'EOF'
from service.events import (
    SessionStarted, TurnStarted, TurnCompleted, TurnFailed, TurnCancelled,
    StageStarted, StageProgress, StageCompleted,
    TokenChunk, MessageComplete, ToolCallStarted, ToolCallCompleted,
    SessionEnded, event_from_dict, event_to_json, event_from_json,
)
events = [
    SessionStarted(session_id="s1", resumed=False),
    SessionEnded(session_id="s1"),
    TurnStarted(session_id="s1", turn_id="t1", message_preview="hello"),
    TurnCompleted(session_id="s1", turn_id="t1", elapsed_ms=1200),
    TurnFailed(session_id="s1", turn_id="t1", error="oops"),
    TurnCancelled(session_id="s1", turn_id="t1", at_stage="ToolLoop"),
    StageStarted(session_id="s1", turn_id="t1", stage="PlanningStage"),
    StageProgress(session_id="s1", turn_id="t1", stage="PlanningStage", message="step 1"),
    StageCompleted(session_id="s1", turn_id="t1", stage="PlanningStage", duration_ms=450),
    TokenChunk(session_id="s1", turn_id="t1", text="hello "),
    MessageComplete(session_id="s1", turn_id="t1", text="hello world"),
    ToolCallStarted(session_id="s1", turn_id="t1", tool_name="read_file", tool_call_id="tc1"),
    ToolCallCompleted(session_id="s1", turn_id="t1", tool_name="read_file", tool_call_id="tc1"),
]
for e in events:
    rt = event_from_json(event_to_json(e))
    assert rt.type == e.type, f"type mismatch: {rt.type} != {e.type}"
    assert rt.session_id == e.session_id
print(f"All {len(events)} event types round-trip correctly.")
EOF

# 3. Protocol structural check (runtime_checkable)
python -c "
from service.interface import AgentService, TurnHandle
# A class satisfying the protocol should pass isinstance() if runtime_checkable
print('Protocol defined — structural check ok')
"
```

## Done when

- [ ] `src/service/__init__.py`, `src/service/events.py`, `src/service/interface.py`,
      `src/service/errors.py` all created.
- [ ] All 13 event types round-trip through `event_to_json` / `event_from_json`.
- [ ] `AgentService` and `TurnHandle` are `Protocol` classes with `@runtime_checkable`.
- [ ] `TurnCancelledError` and `TurnFailedError` defined and exported.
- [ ] No runtime dependencies on `runtime/`, `agent.py`, or `ui/` in this package.
- [ ] `pytest` still green (no existing tests should be affected).

## Out of scope for this phase

- Concrete implementations of `AgentService` (Phase 0083c).
- The `BoundedDropQueue` (Phase 0083c).
- Any `RuntimeEvent` → `AgentEvent` translation (Phase 0083c).
- Textual imports (Phases 0083f+).
