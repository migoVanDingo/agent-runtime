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
        """True while a turn is in flight."""
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
        """Request that the running turn pause at the next checkpoint."""
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
