"""Contextvars threading parent-agent state into sub-agent dispatch.

The BaseTool ``execute(tool_input)`` signature has no parent reference — and
we don't want to change every tool's signature just to enable sub-agent
dispatch. So we use contextvars: ``Agent.call()`` sets these for the
duration of the turn; ``SubAgentTool.execute()`` reads them when invoked.

This is the same pattern as ``runtime.scope`` — process-wide context that
makes per-thread state available across modules without explicit threading.
"""
from __future__ import annotations

import contextlib
import contextvars
from typing import Any, Callable, Generator


_parent_agent: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "arc_parent_agent", default=None,
)
_pause_check: contextvars.ContextVar[Callable[[], None] | None] = contextvars.ContextVar(
    "arc_pause_check", default=None,
)
_parent_turn_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "arc_parent_turn_id", default=None,
)


def current_parent_agent() -> Any:
    return _parent_agent.get()


def current_pause_check() -> Callable[[], None] | None:
    return _pause_check.get()


def current_parent_turn_id() -> str | None:
    return _parent_turn_id.get()


@contextlib.contextmanager
def parent_context(
    *,
    agent: Any,
    pause_check: Callable[[], None] | None = None,
    turn_id: str | None = None,
) -> Generator[None, None, None]:
    """Set parent-agent contextvars for the duration of the block."""
    agent_tok = _parent_agent.set(agent)
    pause_tok = _pause_check.set(pause_check)
    turn_tok = _parent_turn_id.set(turn_id)
    try:
        yield
    finally:
        _parent_agent.reset(agent_tok)
        _pause_check.reset(pause_tok)
        _parent_turn_id.reset(turn_tok)
