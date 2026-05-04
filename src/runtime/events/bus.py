"""Small structured event bus.

The bus is intentionally simple: runtime components emit RuntimeEvent objects,
and sinks decide where to write them. An optional redactor scrubs sensitive
content before delivery to sinks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from runtime.events.schema import RuntimeEvent


class EventSink(Protocol):
    def emit(self, event: RuntimeEvent) -> None:
        ...


class NoopEventSink:
    def emit(self, event: RuntimeEvent) -> None:
        return None


class JsonlEventSink:
    """Append events to a session-scoped JSONL file."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def emit(self, event: RuntimeEvent) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{event.identity.session_id}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
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

    @classmethod
    def noop(cls) -> "EventBus":
        return cls([NoopEventSink()], enabled=False)

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
