# 0083b — EventBus subscribe()

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §3.7.
> This phase is independent of 0083a. It can land in parallel.

## Goal

Add `subscribe(callback)` and `unsubscribe(callback)` to the existing
`EventBus` in `src/runtime/events/bus.py`. The `InProcessAgentService`
(Phase 0083c) will subscribe to receive every `RuntimeEvent` so it can
translate them to `AgentEvent`s.

The subscriber contract:
- Callbacks are invoked **synchronously** on the emitting thread, immediately
  after sinks, in subscription order.
- Callbacks must be O(1) — they should only enqueue, never block.
- Exceptions are swallowed (same policy as sinks).
- The callback list is protected by a lock because sinks may be called
  from any thread (worker thread in 0083c, test threads, etc.).

## File to modify

| File | Change |
|------|--------|
| `src/runtime/events/bus.py` | Add subscriber list + `subscribe`/`unsubscribe` + call in `emit()` |

No other files change in this phase.

## Detailed changes

### Current `EventBus.__init__` signature (from `src/runtime/events/bus.py:39-48`)

```python
class EventBus:
    def __init__(
        self,
        sinks: list[EventSink] | None = None,
        enabled: bool = True,
        redact_on_emit: bool = False,
    ) -> None:
        self._sinks = sinks if sinks is not None else [NoopEventSink()]
        self._enabled = enabled
        self._redact_on_emit = redact_on_emit
```

### Target `EventBus` (full replacement of the class)

```python
import threading
from typing import Callable

class EventBus:
    """Structured event bus for runtime telemetry.

    Two delivery mechanisms:
      Sinks       — push-only, configured at construction (e.g., JSONL file).
      Subscribers — callbacks registered at runtime (e.g., InProcessAgentService).

    Both are called synchronously on the emitting thread. Callbacks must be
    O(1) — they should enqueue and return; never block inside a callback.
    Exceptions in either sinks or subscribers are swallowed so agent execution
    is never interrupted by telemetry errors.
    """

    def __init__(
        self,
        sinks: list[EventSink] | None = None,
        enabled: bool = True,
        redact_on_emit: bool = False,
    ) -> None:
        self._sinks = sinks if sinks is not None else [NoopEventSink()]
        self._enabled = enabled
        self._redact_on_emit = redact_on_emit
        # Subscriber list is mutable at runtime; protect with a lock because
        # subscribe/unsubscribe may be called from the main thread while emit()
        # runs on a worker thread.
        self._subscribers: list[Callable[[RuntimeEvent], None]] = []
        self._sub_lock = threading.Lock()

    @classmethod
    def noop(cls) -> "EventBus":
        """Return a disabled bus with no sinks. Subscribers can still be added."""
        return cls([NoopEventSink()], enabled=False)

    def subscribe(self, callback: Callable[[RuntimeEvent], None]) -> None:
        """Register a callback to be called on every emitted event.

        The callback is invoked on the emitting thread synchronously after
        sinks. It must be O(1) — only enqueue, never block.
        """
        with self._sub_lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[RuntimeEvent], None]) -> None:
        """Remove a previously registered callback. Silent no-op if not found."""
        with self._sub_lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

    def emit(self, event: RuntimeEvent) -> None:
        if not self._enabled:
            return
        if self._redact_on_emit:
            from runtime.events.redactor import get_redactor
            event = get_redactor().redact_event(event)

        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception:
                pass  # never let event emission crash the agent

        # Snapshot the subscriber list under the lock, then call outside the
        # lock so unsubscribe() during a callback can't deadlock.
        with self._sub_lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(event)
            except Exception:
                pass  # subscriber errors are also swallowed
```

### Add import at top of `bus.py`

Add `import threading` and `from typing import Callable` to the existing
imports block. The file currently imports `json`, `Path`, `Protocol`, and
`RuntimeEvent` — add threading and Callable alongside them.

## Verification

```bash
# 1. Existing tests still pass
pytest -x -q

# 2. New unit test (create if it doesn't exist)
python - <<'EOF'
import threading
from runtime.events.bus import EventBus
from runtime.events.schema import RuntimeEvent
from runtime.identity import RuntimeIdentity

# Minimal identity for testing
identity = RuntimeIdentity(session_id="test", project_id="test")

received = []

def cb(event: RuntimeEvent) -> None:
    received.append(event.event_type)

bus = EventBus()
bus.subscribe(cb)
bus.emit(RuntimeEvent("test.event", identity))
bus.emit(RuntimeEvent("test.event2", identity))
assert received == ["test.event", "test.event2"], f"got {received}"

# Unsubscribe stops delivery
bus.unsubscribe(cb)
bus.emit(RuntimeEvent("test.event3", identity))
assert len(received) == 2, "unsubscribe should have stopped delivery"

# Double-subscribe is idempotent
bus.subscribe(cb)
bus.subscribe(cb)
bus.emit(RuntimeEvent("test.event4", identity))
assert received.count("test.event4") == 1, "double-subscribe should not double-fire"

# Noop bus does not deliver
received.clear()
noop = EventBus.noop()
noop.subscribe(cb)
noop.emit(RuntimeEvent("noop.event", identity))
assert received == [], "noop bus should not deliver"

print("All subscribe/unsubscribe assertions passed.")
EOF

# 3. Thread-safety smoke test
python - <<'EOF'
import threading
from runtime.events.bus import EventBus
from runtime.events.schema import RuntimeEvent
from runtime.identity import RuntimeIdentity

identity = RuntimeIdentity(session_id="t", project_id="t")
bus = EventBus()
counts = []

def listener(e):
    counts.append(1)

bus.subscribe(listener)

def emitter():
    for _ in range(50):
        bus.emit(RuntimeEvent("x", identity))

threads = [threading.Thread(target=emitter) for _ in range(4)]
for t in threads: t.start()
for t in threads: t.join()

assert len(counts) == 200, f"expected 200, got {len(counts)}"
print(f"Thread-safety ok: {len(counts)} events delivered.")
EOF
```

## Done when

- [ ] `EventBus` has `_subscribers`, `_sub_lock`, `subscribe()`, `unsubscribe()`.
- [ ] `emit()` calls subscribers after sinks, swallowing exceptions.
- [ ] `noop()` class method still works and returns a disabled bus.
- [ ] `subscribe()` is idempotent (double-registering does not double-fire).
- [ ] `unsubscribe()` is a no-op if the callback was not registered.
- [ ] Thread-safety verified: concurrent `emit()` calls do not corrupt the subscriber list.
- [ ] `pytest` green — all existing tests pass.

## Out of scope for this phase

- Any translation of `RuntimeEvent` to `AgentEvent` (Phase 0083c).
- Adding subscribers from the service layer (Phase 0083c).
- Changing how sinks work — they are unchanged.
