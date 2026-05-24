"""Recursion tripwire.

Layer 2 of the recursion prohibition (Layer 1 is the registry filter in
AgentSession that skips SubAgentTool registration when inside a child).

The tripwire is a ContextVar set when a SubAgentRunner is about to spawn
a child. Any attempt to dispatch another sub-agent from inside that scope
raises SubAgentRecursionError immediately.

Both layers exist on purpose — they catch different failure modes
(forgotten registry filter vs. clever bypass).
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_inside_subagent: ContextVar[bool] = ContextVar("arc_inside_subagent", default=False)


def inside_subagent() -> bool:
    """True when execution is inside a sub-agent's session."""
    return _inside_subagent.get()


@contextmanager
def subagent_scope() -> Iterator[None]:
    """Mark the dynamic scope as 'inside a sub-agent'. Restores on exit."""
    tok = _inside_subagent.set(True)
    try:
        yield
    finally:
        _inside_subagent.reset(tok)
