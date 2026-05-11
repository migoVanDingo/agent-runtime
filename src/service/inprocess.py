"""InProcessAgentService — wraps agent.call() for use by any frontend.

Runs the agent on a ThreadPoolExecutor worker thread. Bridges:
  - on_token callbacks → TokenChunk events  (call_soon_threadsafe)
  - RuntimeEvent bus   → AgentEvent queues  (call_soon_threadsafe)
  - Escalation prompts → TUIUserGate        (blocks worker, TUI supplies answer)

Pause/cancel are stubbed in this phase (0083c); implemented in 0083e.
"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator

from agent import Agent
from runtime.escalation import Escalation
from runtime.events import get_event_bus
from runtime.events.schema import RuntimeEvent
from service.errors import TurnCancelledError, TurnFailedError
from service.events import (
    AgentEvent,
    TurnStarted, TurnCompleted, TurnFailed, TurnCancelled,
    TokenChunk, MessageComplete,
)
from service.queue import BoundedDropQueue
from service.translator import translate


# ── NoopSpinner ───────────────────────────────────────────────────────────────

class NoopSpinner:
    """Drop-in replacement for src/ui/spinner.py:Spinner.

    Silences agent.spinner calls during TUI turns. Agent.spinner remains for
    the legacy arc CLI path.
    """

    def begin_turn(self) -> None:
        pass

    def elapsed_display(self) -> str:
        return ""

    def start(self, message: str = "") -> None:
        pass

    def update(self, message: str = "") -> None:
        pass

    def stop(self) -> None:
        pass


# ── TUIUserGate ───────────────────────────────────────────────────────────────

class TUIUserGate:
    """Escalation gate that blocks the worker thread until the TUI responds.

    The worker thread calls prompt(), which blocks on a threading.Event.
    The TUI (on the async event loop) calls supply_answer(approved) to
    unblock it.

    The service holds a reference to the active TUIUserGate so the TUI can
    reach it via service.user_gate.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._answer: bool = False
        self.pending_escalation: Escalation | None = None

    def prompt(self, escalation: Escalation) -> bool:
        """Called from worker thread. Blocks until supply_answer() is called."""
        self.pending_escalation = escalation
        self._event.clear()
        self._event.wait()  # block worker thread
        self.pending_escalation = None
        return self._answer

    def supply_answer(self, approved: bool) -> None:
        """Called from the TUI (any thread). Unblocks the worker thread."""
        self._answer = approved
        self._event.set()


# ── TUIInputGate ─────────────────────────────────────────────────────────────

class TUIInputGate:
    """Blocks the worker thread on ASK_USER; TUI supplies the clarification response.

    The worker thread calls ask(question), which blocks on a threading.Event.
    The TUI checks pending_question each turn and routes the next submission to
    supply_answer() instead of service.send().
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._answer: str = ""
        self.pending_question: str | None = None

    def ask(self, question: str) -> str:
        """Called from worker thread. Blocks until supply_answer() is called."""
        self.pending_question = question
        self._event.clear()
        self._event.wait()
        self.pending_question = None
        return self._answer

    def supply_answer(self, text: str) -> None:
        """Called from the TUI (any thread). Unblocks the worker thread."""
        self._answer = text
        self._event.set()


# ── TurnHandle implementation ─────────────────────────────────────────────────

class _TurnHandleImpl:
    """Concrete TurnHandle. Created by InProcessAgentService.send()."""

    def __init__(self, turn_id: str, service: "InProcessAgentService") -> None:
        self._turn_id = turn_id
        self._service = service
        self._done: asyncio.Event = asyncio.Event()
        self._result: str = ""
        self._error: Exception | None = None

    @property
    def turn_id(self) -> str:
        return self._turn_id

    def events(self) -> AsyncIterator[AgentEvent]:
        """Return an async iterator of events scoped to this turn."""
        return self._iter_events()

    async def _iter_events(self) -> AsyncIterator[AgentEvent]:
        q: BoundedDropQueue = BoundedDropQueue()
        self._service._add_subscriber_queue(q)
        try:
            async for event in q:
                # Yield session-level and turn-scoped events.
                if event.turn_id is None or event.turn_id == self._turn_id:
                    yield event
                # Stop when this turn's lifecycle ends.
                if event.type in ("turn.completed", "turn.failed", "turn.cancelled"):
                    if event.turn_id == self._turn_id:
                        break
        finally:
            self._service._remove_subscriber_queue(q)
            await q.close()

    async def wait(self) -> str:
        """Block until turn completes. Returns full response text."""
        await self._done.wait()
        if isinstance(self._error, TurnCancelledError):
            raise self._error
        if self._error is not None:
            raise TurnFailedError(str(self._error))
        return self._result

    async def cancel(self) -> None:
        await self._service.cancel_current_turn()

    def _resolve(self, result: str) -> None:
        self._result = result
        self._done.set()

    def _reject(self, error: Exception) -> None:
        self._error = error
        self._done.set()


# ── InProcessAgentService ─────────────────────────────────────────────────────

class InProcessAgentService:
    """AgentService implementation that wraps agent.call() in a thread executor.

    Thread model:
      Main thread / event loop: all async methods, event delivery to queues.
      Worker thread (ThreadPoolExecutor, 1 worker): agent.call() runs here.
      Cross-thread hops: _publish_threadsafe() uses loop.call_soon_threadsafe
        to post events from the worker thread onto the main event loop.

    Caller responsibility:
      The Agent should be constructed with user_gate=TUIUserGate() before
      being passed to this service. The same TUIUserGate instance is stored
      as service.user_gate so the TUI can call supply_answer() on escalation.
    """

    def __init__(self, agent: Agent, session_id: str) -> None:
        self._agent = agent
        self._session_id = session_id
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="arc-agent")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._is_busy = False
        self._current_handle: _TurnHandleImpl | None = None

        # Pause/cancel use threading primitives because agent.call() runs on a
        # worker thread, not the event loop.
        # _pause_event: set = running, cleared = paused.
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._cancel_event = threading.Event()

        # All consumer queues — one per active events() or handle.events() consumer.
        self._queues: list[BoundedDropQueue] = []
        self._queues_lock = threading.Lock()

        # Replace spinner so agent.spinner calls don't write to stdout under the TUI.
        self._agent.spinner = NoopSpinner()

        # Expose user_gate for the TUI to call supply_answer() on escalation.
        # If the agent was constructed with a TUIUserGate, store that reference.
        # Otherwise no escalation bridging is available (legacy path).
        if isinstance(getattr(agent, "user_gate", None), TUIUserGate):
            self.user_gate: TUIUserGate | None = agent.user_gate  # type: ignore[assignment]
        else:
            self.user_gate = None

        # input_gate is set by build_service() after construction to wire ASK_USER.
        self.input_gate: TUIInputGate | None = None

        # Subscribe to the runtime event bus for stage/tool translation.
        # Callback fires on the worker thread → always use call_soon_threadsafe.
        get_event_bus().subscribe(self._on_runtime_event)

    # ── Public protocol ───────────────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    def events(self) -> AsyncIterator[AgentEvent]:
        """Global event stream. Subscribe once; yields until close()."""
        q: BoundedDropQueue = BoundedDropQueue()
        self._add_subscriber_queue(q)
        return self._drain_queue(q)

    async def _drain_queue(self, q: BoundedDropQueue) -> AsyncIterator[AgentEvent]:
        try:
            async for event in q:
                yield event
        finally:
            self._remove_subscriber_queue(q)

    async def send(self, message: str) -> _TurnHandleImpl:
        if self._is_busy:
            raise RuntimeError("Agent is busy — queue the message at the UI layer")

        self._loop = asyncio.get_event_loop()
        self._is_busy = True

        turn_id = f"turn-{int(time.monotonic() * 1000)}"
        handle = _TurnHandleImpl(turn_id=turn_id, service=self)
        self._current_handle = handle

        # Publish TurnStarted synchronously before kicking off the thread.
        await self._publish(TurnStarted(
            session_id=self._session_id,
            turn_id=turn_id,
            message_preview=message[:300],
        ))

        asyncio.ensure_future(self._run_turn(message, turn_id, handle))
        return handle

    async def _run_turn(
        self,
        message: str,
        turn_id: str,
        handle: _TurnHandleImpl,
    ) -> None:
        """Drive agent.call() via run_in_executor; bridge events back to consumers."""
        t0 = time.monotonic()

        # Snapshot the token tracker so we can report per-turn deltas in the
        # TurnCompleted event (UI displays them next to the timer).
        try:
            from runtime.token_tracker import get_tracker
            _tracker = get_tracker()
            _tokens_in_before = _tracker._session_input
            _tokens_out_before = _tracker._session_output
        except Exception:
            _tracker = None
            _tokens_in_before = _tokens_out_before = 0

        try:
            def on_token(chunk: str) -> None:
                self._publish_threadsafe(TokenChunk(
                    session_id=self._session_id,
                    turn_id=turn_id,
                    text=chunk,
                ))

            # Reset cancel flag before each new turn.
            self._cancel_event.clear()
            self._pause_event.set()

            response = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                lambda: self._agent.call(
                    message,
                    on_token=on_token,
                    checkpoint_fn=self.checkpoint,
                ),
            )

            elapsed_ms = int((time.monotonic() - t0) * 1000)

            if _tracker is not None:
                tokens_in = _tracker._session_input - _tokens_in_before
                tokens_out = _tracker._session_output - _tokens_out_before
            else:
                tokens_in = tokens_out = 0

            await self._publish(MessageComplete(
                session_id=self._session_id,
                turn_id=turn_id,
                text=response,
            ))
            await self._publish(TurnCompleted(
                session_id=self._session_id,
                turn_id=turn_id,
                response_preview=response[:500],
                elapsed_ms=elapsed_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            ))
            handle._resolve(response)

        except TurnCancelledError as exc:
            await self._publish(TurnCancelled(
                session_id=self._session_id,
                turn_id=turn_id,
                at_stage=exc.at_stage,
            ))
            handle._reject(exc)

        except Exception as exc:
            await self._publish(TurnFailed(
                session_id=self._session_id,
                turn_id=turn_id,
                error=str(exc)[:500],
            ))
            handle._reject(TurnFailedError(str(exc)))

        finally:
            self._is_busy = False
            self._current_handle = None

    # ── Checkpoint (called from worker thread) ────────────────────────────────

    def checkpoint(self) -> None:
        """Called synchronously from the worker thread at yield points.

        Checks cancel first (fast path). Then blocks on the pause event if
        paused. Uses a poll loop with timeout so a cancel signal arriving
        while paused is detected promptly without indefinite blocking.

        This method must never be called from the event loop — it blocks.
        """
        if self._cancel_event.is_set():
            self._cancel_event.clear()
            raise TurnCancelledError(at_stage="checkpoint")
        # Poll the pause event with a short timeout so cancel signals are
        # detected even while paused.
        while not self._pause_event.wait(timeout=0.2):
            if self._cancel_event.is_set():
                self._cancel_event.clear()
                raise TurnCancelledError(at_stage="checkpoint-while-paused")

    # ── Pause / cancel (called from event loop) ───────────────────────────────

    async def pause(self) -> None:
        """Request that the worker thread pause at the next checkpoint."""
        self._pause_event.clear()  # threading.Event — safe from any thread

    async def resume(self) -> None:
        """Resume a paused turn."""
        self._pause_event.set()

    async def cancel_current_turn(self) -> None:
        """Cancel the in-flight turn.

        Sets the cancel flag, then unblocks the pause event in case the worker
        thread is sitting in checkpoint() waiting for resume. The worker thread
        will see the cancel flag on its next checkpoint() call.
        """
        if not self._is_busy:
            return
        self._cancel_event.set()
        self._pause_event.set()  # unblock paused worker so it can detect cancel

    # ── Utility ───────────────────────────────────────────────────────────────

    def conversation_history(self) -> list[dict]:
        return self._agent.messenger.get_messages()

    def list_resumable_sessions(self, limit: int = 20) -> list:
        """List resumable sessions from the artifact store."""
        try:
            from runtime.artifact_store import get_artifact_store
            store = get_artifact_store()
            return store.list_resumable_sessions(limit=limit)
        except Exception:
            return []

    def load_conversation(self, session_id: str) -> list[dict]:
        """Load conversation history from the artifact store and inject into agent."""
        try:
            from runtime.artifact_store import get_artifact_store
            store = get_artifact_store()
            messages = store.load_conversation(session_id)
            # Cap at 30 messages to avoid context overflow on resume
            cap = 30
            if len(messages) > cap:
                messages = messages[-cap:]
            self._agent.messenger.get_messages().clear()
            self._agent.messenger.get_messages().extend(messages)
            return messages
        except Exception:
            return []

    async def close(self) -> None:
        """Shut down the service cleanly."""
        # Unblock any pending escalation so the worker thread can exit.
        if self.user_gate is not None and self.user_gate.pending_escalation:
            self.user_gate.supply_answer(False)  # deny by default on session close
        # Unblock any pending ASK_USER question so the worker thread can exit.
        if self.input_gate is not None and self.input_gate.pending_question:
            self.input_gate.supply_answer("")
        self._cancel_event.set()
        self._pause_event.set()  # unblock paused worker
        get_event_bus().unsubscribe(self._on_runtime_event)
        self._executor.shutdown(wait=False)
        with self._queues_lock:
            qs = list(self._queues)
        for q in qs:
            await q.close()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _add_subscriber_queue(self, q: BoundedDropQueue) -> None:
        with self._queues_lock:
            self._queues.append(q)

    def _remove_subscriber_queue(self, q: BoundedDropQueue) -> None:
        with self._queues_lock:
            try:
                self._queues.remove(q)
            except ValueError:
                pass

    async def _publish(self, event: AgentEvent) -> None:
        """Publish to all subscriber queues. Must be called from the event loop."""
        with self._queues_lock:
            qs = list(self._queues)
        for q in qs:
            await q.put(event)

    def _publish_threadsafe(self, event: AgentEvent) -> None:
        """Publish from the worker thread by hopping to the event loop."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            lambda e=event: asyncio.ensure_future(self._publish(e))
        )

    def _on_runtime_event(self, raw: RuntimeEvent) -> None:
        """Bus subscriber. Fires on the worker thread — must be O(1)."""
        translated = translate(raw, self._session_id)
        if translated is not None:
            self._publish_threadsafe(translated)
