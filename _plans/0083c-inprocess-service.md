# 0083c — InProcessAgentService + translator + queue

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §3.3, §3.4, §3.5.
> Depends on: **0083a** (types), **0083b** (bus subscribe).

## Goal

Implement the `InProcessAgentService` — the concrete implementation of
`AgentService` that wraps the existing sync `agent.call()` in a thread
executor. This phase also implements:

- `service/translator.py` — pure function mapping `RuntimeEvent` → `AgentEvent | None`
- `service/queue.py` — `BoundedDropQueue` with selective lifecycle-event protection
- `NoopSpinner` — replaces `agent.spinner` so stages don't write to stdout
- `TUIUserGate` — replaces `CLIUserGate` for escalation prompts in TUI context

Pause/cancel yield points are **not** implemented here — those come in Phase 0083e.
`pause()`, `resume()`, and `cancel_current_turn()` exist as stubs that return
immediately.

## Files to create

| File | Purpose |
|------|---------|
| `src/service/queue.py` | `BoundedDropQueue` — asyncio queue with selective drop on overflow |
| `src/service/translator.py` | `translate(RuntimeEvent) -> AgentEvent | None` pure function |
| `src/service/inprocess.py` | `InProcessAgentService` implementation |

No existing files are modified in this phase (except exporting the new class
from `src/service/__init__.py`).

## Detailed implementation

### `src/service/queue.py`

```python
"""Bounded async queue with selective drop policy.

On overflow, only TokenChunk events are dropped (oldest first). Lifecycle
events (session/turn/stage/tool) are never dropped — the UI's state machine
depends on them arriving exactly once.

Usage:
    q = BoundedDropQueue(maxsize=1000)
    await q.put(event)           # non-blocking; may drop oldest TokenChunk
    async for event in q:        # yields indefinitely until q.close() called
        dispatch(event)
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import AsyncIterator

from service.events import AgentEvent, TokenChunk

# Event types that must never be dropped.
_LIFECYCLE_TYPES = frozenset({
    "session.started", "session.ended",
    "turn.started", "turn.completed", "turn.failed", "turn.cancelled",
    "stage.started", "stage.progress", "stage.completed",
    "tool.call.started", "tool.call.completed",
})


class BoundedDropQueue:
    """Async queue that protects lifecycle events from overflow drops.

    drop_count is incremented each time a TokenChunk is discarded. The TUI
    reads this to show a throttled indicator in the status bar.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        self._maxsize = maxsize
        self._queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self._buffer: deque[AgentEvent] = deque()
        self._lock = asyncio.Lock()
        self._closed = False
        self.drop_count = 0

    async def put(self, event: AgentEvent) -> None:
        """Enqueue an event. Never blocks; may drop oldest TokenChunk on overflow."""
        if self._closed:
            return
        async with self._lock:
            if len(self._buffer) >= self._maxsize:
                # Find and remove the oldest droppable (TokenChunk) event.
                for i, buffered in enumerate(self._buffer):
                    if isinstance(buffered, TokenChunk):
                        del self._buffer[i]
                        self.drop_count += 1
                        break
                # If no TokenChunk found, the buffer is full of lifecycle events;
                # still enqueue — callers should not send that many lifecycle
                # events without consuming. In practice this cannot happen.
            self._buffer.append(event)
            await self._queue.put(event)

    async def __aiter__(self) -> AsyncIterator[AgentEvent]:
        """Yield events until close() is called."""
        while True:
            item = await self._queue.get()
            if item is None:
                return  # sentinel from close()
            yield item

    async def close(self) -> None:
        """Signal end-of-stream to all consumers."""
        self._closed = True
        await self._queue.put(None)  # sentinel
```

### `src/service/translator.py`

A pure function with no side effects. Receives a `RuntimeEvent` and returns
the corresponding `AgentEvent` or `None` if the event is not surfaced to
the UI.

```python
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
    kwargs = dict(session_id=session_id, turn_id=turn_id)

    t = event.event_type

    if t == "session.started":
        return SessionStarted(**kwargs, resumed=False, session_dir="")
    if t == "session.resumed":
        return SessionStarted(**kwargs, resumed=True, session_dir="")
    if t == "session.ended":
        return SessionEnded(**kwargs)
    if t == "turn.started":
        return TurnStarted(**kwargs, message_preview=str(p.get("message_preview", ""))[:300])

    # turn.completed / turn.failed are suppressed — emitted by the service driver.
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
        args_raw = p.get("tool_input", {})
        args_preview = json.dumps(args_raw, default=str)[:200] if args_raw else ""
        return ToolCallStarted(
            **kwargs,
            tool_name=str(p.get("tool_name", "")),
            tool_call_id=str(p.get("tool_call_id", "")),
            args_preview=args_preview,
        )
    if t == "tool.call.completed":
        return ToolCallCompleted(
            **kwargs,
            tool_name=str(p.get("tool_name", "")),
            tool_call_id=str(p.get("tool_call_id", "")),
            result_preview=str(p.get("result_preview", ""))[:200],
            error=str(p.get("error", "")),
        )

    # All other event types (escalation.requested, escalation.resolved, etc.)
    # are not surfaced to the UI in this version.
    return None
```

### `src/service/inprocess.py`

The most complex file in this phase. Key design decisions documented inline.

```python
"""InProcessAgentService — wraps agent.call() for use by any frontend.

Runs the agent on a ThreadPoolExecutor worker thread. Bridges:
  - on_token callbacks → TokenChunk events (call_soon_threadsafe)
  - RuntimeEvent bus   → AgentEvent queue (call_soon_threadsafe)
  - ASK_USER flow      → blocks worker thread; TUI supplies answer via queue

Pause/cancel are stubbed in this phase (0083c); implemented in 0083e.
"""
from __future__ import annotations

import asyncio
import time
import threading
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
    SessionStarted, SessionEnded,
)
from service.interface import AgentService, TurnHandle
from service.queue import BoundedDropQueue
from service.translator import translate


# ── NoopSpinner ───────────────────────────────────────────────────────────────

class NoopSpinner:
    """Drop-in replacement for src/ui/spinner.py:Spinner.

    Injected into agent.spinner before any turn so that stage code that calls
    spinner.start/stop/update does not write to stdout while the TUI is running.

    TODO(0083-cleanup): Remove spinner from stage signatures entirely.
    All stage-level status is now surfaced via StageStarted/StageCompleted events.
    """

    def begin_turn(self) -> None: pass
    def elapsed_display(self) -> str: return ""
    def start(self, message: str = "") -> None: pass
    def update(self, message: str = "") -> None: pass
    def stop(self) -> None: pass


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
        self._event.wait()   # block worker thread
        self.pending_escalation = None
        return self._answer

    def supply_answer(self, approved: bool) -> None:
        """Called from the TUI (any thread). Unblocks the worker thread."""
        self._answer = approved
        self._event.set()


# ── TurnHandleImpl ────────────────────────────────────────────────────────────

class _TurnHandleImpl:
    """Concrete TurnHandle. Created by InProcessAgentService.send()."""

    def __init__(
        self,
        turn_id: str,
        service: "InProcessAgentService",
    ) -> None:
        self._turn_id = turn_id
        self._service = service
        self._done = asyncio.Event()
        self._result: str = ""
        self._error: Exception | None = None

    @property
    def turn_id(self) -> str:
        return self._turn_id

    async def events(self) -> AsyncIterator[AgentEvent]:
        """Filter global service events to this turn only."""
        sub_queue = BoundedDropQueue()
        self._service._add_subscriber_queue(sub_queue)
        try:
            async for event in sub_queue:
                if event.turn_id == self._turn_id or event.turn_id is None:
                    yield event
                # Stop streaming when the turn lifecycle ends.
                if event.type in (
                    "turn.completed", "turn.failed", "turn.cancelled"
                ) and event.turn_id == self._turn_id:
                    break
        finally:
            self._service._remove_subscriber_queue(sub_queue)
            await sub_queue.close()

    async def wait(self) -> str:
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
      - Main thread / event loop: all async methods, event delivery to queues.
      - Worker thread (ThreadPoolExecutor, 1 worker): agent.call() runs here.
      - Cross-thread hops: _publish_threadsafe() uses loop.call_soon_threadsafe
        to post events from the worker thread onto the main event loop.
    """

    def __init__(self, agent: Agent, session_id: str) -> None:
        self._agent = agent
        self._session_id = session_id
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="arc-agent")
        self._loop: asyncio.AbstractEventLoop | None = None  # set in send()
        self._is_busy = False
        self._current_handle: _TurnHandleImpl | None = None

        # All consumer queues — one per UI consumer (global + per-turn handles).
        self._queues: list[BoundedDropQueue] = []
        self._queues_lock = threading.Lock()

        # Replace spinner so stages don't write to stdout.
        # TODO(0083-cleanup): remove spinner from stage signatures.
        self._agent.spinner = NoopSpinner()

        # Subscribe to the runtime event bus for stage/tool events.
        # Callback fires on worker thread → use call_soon_threadsafe.
        get_event_bus().subscribe(self._on_runtime_event)

        # TUI user gate replaces CLIUserGate for escalation prompts.
        self.user_gate = TUIUserGate()

    # ── Public protocol ───────────────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    def events(self) -> AsyncIterator[AgentEvent]:
        """Global event stream. Subscribe once; yields forever until close()."""
        q = BoundedDropQueue()
        self._add_subscriber_queue(q)
        return self._drain_queue(q)

    async def _drain_queue(self, q: BoundedDropQueue) -> AsyncIterator[AgentEvent]:
        async for event in q:
            yield event

    async def send(self, message: str) -> TurnHandle:
        if self._is_busy:
            raise RuntimeError("Agent is busy — queue the message at the UI layer")

        self._loop = asyncio.get_event_loop()
        self._is_busy = True

        # Generate a turn ID consistent with the identity system.
        import time as _t
        turn_id = f"turn-{int(_t.time() * 1000)}"

        handle = _TurnHandleImpl(turn_id=turn_id, service=self)
        self._current_handle = handle

        # Publish TurnStarted before the thread starts so the UI sees it
        # immediately even if the thread pool is momentarily busy.
        await self._publish(TurnStarted(
            session_id=self._session_id,
            turn_id=turn_id,
            message_preview=message[:300],
        ))

        # Run agent.call() on the worker thread.
        asyncio.ensure_future(self._run_turn(message, turn_id, handle))
        return handle

    async def _run_turn(
        self,
        message: str,
        turn_id: str,
        handle: _TurnHandleImpl,
    ) -> None:
        """Drive agent.call() from the event loop via run_in_executor."""
        t0 = time.monotonic()
        try:
            # on_token fires on the worker thread — hop back to the loop.
            def on_token(chunk: str) -> None:
                event = TokenChunk(
                    session_id=self._session_id,
                    turn_id=turn_id,
                    text=chunk,
                )
                self._publish_threadsafe(event)

            response = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                lambda: self._agent.call(message, on_token=on_token),
            )

            elapsed_ms = int((time.monotonic() - t0) * 1000)

            # Emit MessageComplete with the full assembled text.
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

    # ── Pause/cancel stubs (implemented in 0083e) ─────────────────────────────

    async def pause(self) -> None:
        """Stub — implemented in Phase 0083e."""
        pass

    async def resume(self) -> None:
        """Stub — implemented in Phase 0083e."""
        pass

    async def cancel_current_turn(self) -> None:
        """Stub — implemented in Phase 0083e."""
        pass

    # ── Utility ───────────────────────────────────────────────────────────────

    def conversation_history(self) -> list[dict]:
        return self._agent.messenger.get_messages()

    async def close(self) -> None:
        """Shut down the service. Closes all consumer queues."""
        get_event_bus().unsubscribe(self._on_runtime_event)
        self._executor.shutdown(wait=False)
        with self._queues_lock:
            qs = list(self._queues)
        for q in qs:
            await q.close()

    # ── Internal helpers ─────────────────────────────────────────────────────

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
        """Publish an event to all subscriber queues. Call from the event loop."""
        with self._queues_lock:
            qs = list(self._queues)
        for q in qs:
            await q.put(event)

    def _publish_threadsafe(self, event: AgentEvent) -> None:
        """Publish an event from the worker thread. Hops to the event loop."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            lambda e=event: asyncio.ensure_future(self._publish(e))
        )

    def _on_runtime_event(self, raw: RuntimeEvent) -> None:
        """Bus subscriber callback. Fires on the worker thread.

        Translates the RuntimeEvent to an AgentEvent and publishes it.
        Must be O(1) — only enqueue, never block.
        """
        translated = translate(raw, self._session_id)
        if translated is not None:
            self._publish_threadsafe(translated)
```

### Wire `TUIUserGate` into the agent

The `Agent` class constructs its `Pipeline` with a `user_input_fn`. To replace
`CLIUserGate` with `TUIUserGate`, the `InProcessAgentService` must intercept
the gate at agent construction. Check `src/agent.py` for where `CLIUserGate`
is instantiated and how it is passed to the `Pipeline`.

The cleanest approach: after constructing the `Agent`, replace
`agent._pipeline._user_input_fn` if accessible, OR pass a custom
`user_input_fn` when constructing the `Agent`. Read `src/agent.py` to find
the cleanest injection point.

If `Agent.__init__` accepts a `user_gate` parameter, pass `self.user_gate`.
If not, add one as an optional kwarg (default: `CLIUserGate()`). The
`InProcessAgentService.__init__` then passes `TUIUserGate()`.

**NOTE:** This is a narrow change to `src/agent.py` — add an optional
`user_gate: UserGate | None = None` parameter and wire it through. The
legacy `main.py` path passes nothing, so `CLIUserGate` remains the default.

### Export from `src/service/__init__.py`

Add to the existing `__init__.py` from Phase 0083a:

```python
from service.inprocess import InProcessAgentService, NoopSpinner, TUIUserGate
from service.queue import BoundedDropQueue
```

## Verification

```bash
# 1. All existing tests still pass
pytest -x -q

# 2. Service constructs without error against the real agent
python - <<'EOF'
import asyncio
from agent import Agent
from service.inprocess import InProcessAgentService

async def smoke():
    agent = Agent(verbose=False)
    svc = InProcessAgentService(agent, session_id="smoke-test")
    print(f"session_id={svc.session_id}")
    print(f"is_busy={svc.is_busy}")
    await svc.close()
    print("Service construction and close: ok")

asyncio.run(smoke())
EOF

# 3. Full integration test — run via scripts/service_repl.py (Phase 0083d)
# Verify in Phase 0083d with the test harness.
```

## Done when

- [ ] `src/service/queue.py` created: `BoundedDropQueue` with `put()`, async `__aiter__`, `close()`, `drop_count`.
- [ ] `src/service/translator.py` created: `translate()` handles all listed RuntimeEvent types.
- [ ] `src/service/inprocess.py` created: `InProcessAgentService` satisfies `AgentService` Protocol.
- [ ] `NoopSpinner` injected into `agent.spinner` at construction; no stdout from stages.
- [ ] `TUIUserGate` wired in place of `CLIUserGate` when a service is active.
- [ ] `TurnHandle.wait()` returns the full response text on success.
- [ ] `TurnHandle.events()` yields only events for that turn.
- [ ] Bus subscriber registered at construction, unregistered at `close()`.
- [ ] `pause()`, `resume()`, `cancel_current_turn()` exist as stubs (return None).
- [ ] `pytest` green.

## Out of scope for this phase

- Pause/cancel implementation (Phase 0083e).
- The Textual app (Phase 0083f).
- Any changes to `main.py` CLI path.
