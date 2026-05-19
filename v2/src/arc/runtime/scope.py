"""Identity + scope contextvars.

These are the spine of observability. Every event the runtime emits carries
session_id, turn_id, scope, and parent_event_id — all derived from contextvars
so any code (including plugins) can call emit() without explicit threading.

Per design §6.5:
  - session_id never changes within a session
  - turn_id is set on turn start, cleared on turn end
  - scope is "main" by default; sub-agents (later) push "subagent:<name>"
  - parent_event_id supports event causation chains

Use the context managers — never set the contextvars directly. The CMs
restore previous values on exit so nesting works (e.g., a sub-agent inside
a turn inside a session).
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

# Public scope tags. Sub-agents (later) construct "subagent:<spec_name>"
SCOPE_MAIN = "main"

# ── Contextvars ─────────────────────────────────────────────────────────────
# Defaults are None for session/turn so absence is detectable; "main" for scope
# so the common case is implicit.

_session_id: ContextVar[str | None] = ContextVar("arc_session_id", default=None)
_turn_id: ContextVar[str | None] = ContextVar("arc_turn_id", default=None)
_scope: ContextVar[str] = ContextVar("arc_scope", default=SCOPE_MAIN)
_parent_event_id: ContextVar[str | None] = ContextVar("arc_parent_event_id", default=None)


# ── Readers ─────────────────────────────────────────────────────────────────

def current_session_id() -> str | None:
    return _session_id.get()


def current_turn_id() -> str | None:
    return _turn_id.get()


def current_scope() -> str:
    return _scope.get()


def current_parent_event_id() -> str | None:
    return _parent_event_id.get()


# ── Context managers ────────────────────────────────────────────────────────

@contextmanager
def session(session_id: str) -> Iterator[None]:
    """Enter a session scope. Sets session_id for the duration of the block."""
    tok = _session_id.set(session_id)
    try:
        yield
    finally:
        _session_id.reset(tok)


@contextmanager
def turn(turn_id: str) -> Iterator[None]:
    """Enter a turn scope. Nested inside a session."""
    tok = _turn_id.set(turn_id)
    try:
        yield
    finally:
        _turn_id.reset(tok)


@contextmanager
def scoped(name: str) -> Iterator[None]:
    """Push a scope tag (e.g., 'subagent:ghidra_analyst'). Restores on exit."""
    tok = _scope.set(name)
    try:
        yield
    finally:
        _scope.reset(tok)


@contextmanager
def parent_event(event_id: str) -> Iterator[None]:
    """Mark subsequent events as children of `event_id`. Used to chain causation
    (e.g., a tool.call.* event is parented to the llm.call.completed that requested it).
    """
    tok = _parent_event_id.set(event_id)
    try:
        yield
    finally:
        _parent_event_id.reset(tok)
