"""Service-layer event taxonomy for the AgentService contract.

All events emitted by AgentService implementations. Discriminated by the
`type` field string — stable across versions, safe for JSON transport.

Families:
  Session  — session lifecycle
  Turn     — turn lifecycle (one user message → one agent response)
  Stage    — pipeline stage progress (replaces spinner in UI)
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
    """Common fields on every AgentEvent."""
    type: str
    session_id: str
    timestamp: str = field(default_factory=_now_iso)
    turn_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Session family ────────────────────────────────────────────────────────────

@dataclass
class SessionStarted(_EventBase):
    type: str = field(default="session.started", init=False)
    resumed: bool = False
    session_dir: str = ""


@dataclass
class SessionEnded(_EventBase):
    type: str = field(default="session.ended", init=False)
    reason: str = "normal"  # "normal" | "error" | "cancelled"


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
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class TurnFailed(_EventBase):
    type: str = field(default="turn.failed", init=False)
    error: str = ""


@dataclass
class TurnCancelled(_EventBase):
    type: str = field(default="turn.cancelled", init=False)
    at_stage: str = ""  # which checkpoint fired the cancel


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
    status: str = "ok"  # "ok" | "abort" | "done"
    duration_ms: int = 0


# ── Content family ────────────────────────────────────────────────────────────

@dataclass
class TokenChunk(_EventBase):
    """A streamed token chunk. May arrive hundreds of times per turn.

    This is the only high-frequency event; BoundedDropQueue may drop oldest
    TokenChunks on overflow. The full text arrives in MessageComplete.
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
    args_preview: str = ""  # first 200 chars of serialized args


@dataclass
class ToolCallCompleted(_EventBase):
    type: str = field(default="tool.call.completed", init=False)
    tool_name: str = ""
    tool_call_id: str = ""
    result_preview: str = ""  # first 200 chars of result
    error: str = ""  # non-empty if tool raised


# ── Discriminated union ───────────────────────────────────────────────────────

AgentEvent = Union[
    SessionStarted, SessionEnded,
    TurnStarted, TurnCompleted, TurnFailed, TurnCancelled,
    StageStarted, StageProgress, StageCompleted,
    TokenChunk, MessageComplete,
    ToolCallStarted, ToolCallCompleted,
]

# Registry for from_dict dispatch — one entry per concrete event type.
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
    """Reconstruct a typed AgentEvent from a dict (e.g. from JSON transport)."""
    cls = _EVENT_REGISTRY.get(d.get("type", ""))
    if cls is None:
        raise ValueError(f"Unknown AgentEvent type: {d.get('type')!r}")
    # Only include fields that are part of __init__ (exclude init=False fields like `type`).
    known = {
        name for name, f in cls.__dataclass_fields__.items()  # type: ignore[attr-defined]
        if f.init
    }
    return cls(**{k: v for k, v in d.items() if k in known})


def event_to_json(event: AgentEvent) -> str:
    return json.dumps(asdict(event), default=str)  # type: ignore[call-overload]


def event_from_json(s: str) -> AgentEvent:
    return event_from_dict(json.loads(s))
