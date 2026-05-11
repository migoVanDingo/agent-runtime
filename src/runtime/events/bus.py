"""Small structured event bus.

The bus is intentionally simple: runtime components emit RuntimeEvent objects,
and sinks decide where to write them. An optional redactor scrubs sensitive
content before delivery to sinks.

Subscribers (added via subscribe()) receive every event synchronously on the
emitting thread, just like sinks. They must be O(1) — enqueue and return.
Errors are swallowed so a bad subscriber can never crash the agent.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable, Protocol

from runtime.events.schema import RuntimeEvent


class EventSink(Protocol):
    def emit(self, event: RuntimeEvent) -> None:
        ...


class NoopEventSink:
    def emit(self, event: RuntimeEvent) -> None:
        return None


class JsonlEventSink:
    """Append events to a fixed session-scoped JSONL file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def emit(self, event: RuntimeEvent) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")


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
        self._subscribers: list[Callable[[RuntimeEvent], None]] = []
        self._sub_lock = threading.Lock()

    @classmethod
    def noop(cls) -> "EventBus":
        return cls([NoopEventSink()], enabled=False)

    def subscribe(self, callback: Callable[[RuntimeEvent], None]) -> None:
        """Register a callback to receive every emitted RuntimeEvent.

        Callbacks are called synchronously on the emit() thread. They must be
        O(1) — enqueue the event and return. Slow callbacks block the agent.
        """
        with self._sub_lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[RuntimeEvent], None]) -> None:
        """Remove a previously registered callback. No-op if not registered."""
        with self._sub_lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

    def emit(self, event: RuntimeEvent) -> None:
        if not self._enabled:
            # Still notify subscribers even on a noop bus — the service layer
            # subscribes regardless of whether file sinks are enabled.
            with self._sub_lock:
                snapshot = list(self._subscribers)
            for cb in snapshot:
                try:
                    cb(event)
                except Exception:
                    pass
            return
        if self._redact_on_emit:
            from runtime.events.redactor import get_redactor
            event = get_redactor().redact_event(event)
        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception:
                pass  # never let event emission crash the agent
        with self._sub_lock:
            snapshot = list(self._subscribers)
        for cb in snapshot:
            try:
                cb(event)
            except Exception:
                pass
