"""Process-wide scope context — single source of truth for "which agent tier is active".

Three scopes:

- ``"main"`` (default) — the user-facing agent loop, calls the main provider
- ``"runtime"`` — a classifier-style stage (RoutingStage, SkillHintStage,
  ExecutionMonitor, ImportanceScorer) about to call the runtime provider
- ``"subagent:<name>"`` — a child agent dispatched via ``SubAgentRunner``

This scope drives three behaviors that need to agree on "which tier":

1. **Context budget** (`runtime.context.manager.ContextManager.pack`) picks
   a smaller token budget when scope is ``"runtime"`` so haiku-class
   classifier calls don't blow past per-minute rate limits.
2. **Logging** prefixes every log record with the active scope tag, so
   ``session.log`` shows where work is happening at a glance.
3. **Telemetry** stamps every ``RuntimeEvent`` with ``agent_scope`` for
   direct pandas grouping.

Use the ``scoped(name)`` context manager at every place that enters a new
scope. Resetting via the token returned by ``ContextVar.set`` is mandatory
for nested correctness (sub-agent inside a runtime stage, for example).
"""
from __future__ import annotations

import contextlib
import contextvars
from typing import Generator

MAIN: str = "main"
RUNTIME: str = "runtime"


_scope: contextvars.ContextVar[str] = contextvars.ContextVar("arc_scope", default=MAIN)


def current_scope() -> str:
    """Return the active scope. Defaults to ``"main"`` outside any explicit scope."""
    return _scope.get()


def is_subagent_scope(scope: str | None = None) -> bool:
    """True when the given (or current) scope is a sub-agent scope."""
    s = scope if scope is not None else current_scope()
    return s.startswith("subagent:")


@contextlib.contextmanager
def scoped(name: str) -> Generator[str, None, None]:
    """Enter a named scope. Restored on exit, even on exception.

    Nesting is allowed and stacks naturally because each entry returns a
    reset token. Sub-agents inside a runtime-stage call get back to
    runtime, which gets back to main.
    """
    token = _scope.set(name)
    try:
        yield name
    finally:
        _scope.reset(token)
