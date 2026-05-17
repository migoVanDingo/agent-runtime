"""Small structured event bus.

The bus is intentionally simple: runtime components emit RuntimeEvent objects,
and sinks decide where to write them. An optional redactor scrubs sensitive
content before delivery to sinks.

Subscribers (added via subscribe()) receive every event synchronously on the
emitting thread, just like sinks. They must be O(1) — enqueue and return.
Errors are swallowed so a bad subscriber can never crash the agent.

Content paging (schema v2):
- If an event carries large ``content``, the bus computes its serialized size.
  When it exceeds ``blob_inline_threshold_bytes`` and a BlobSink is configured,
  the content is written to a sidecar JSON blob and ``raw_payload_ref`` is set.
  The event delivered to sinks/subscribers has its ``content`` field cleared so
  the JSONL line stays compact.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Protocol

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


class BlobSink:
    """Write large event content to per-event JSON blobs.

    Each blob lives at ``<blob_dir>/<event_id>.json`` and carries:
        {"event_id": ..., "ts": ..., "kind": ..., "data": ...}

    The relative path (``blobs/<event_id>.json``) returned by ``write`` is
    stored on the event as ``raw_payload_ref`` so analysts can join blob files
    back to their parent record.
    """

    def __init__(self, blob_dir: Path) -> None:
        self._dir = blob_dir

    def write(self, event_id: str, ts: str, kind: str, data: Any) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._dir / f"{event_id}.json"
        record = {"event_id": event_id, "ts": ts, "kind": kind, "data": data}
        with open(target, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, default=str)
        # Relative to session_dir → "blobs/<id>.json"
        return f"blobs/{event_id}.json"


def _infer_content_kind(event_type: str) -> str:
    """Map event_type → canonical blob kind for ``content`` paging."""
    mapping = {
        "llm.call.started": "llm.prompt",
        "llm.call.completed": "llm.response",
        "tool.call.started": "tool.input",
        "tool.call.completed": "tool.output",
        "plan.created": "plan.full",
        "plan.replanned": "plan.full",
        "plan.revised": "plan.full",
        "rag.query.returned": "rag.chunks",
        "skill.expanded": "skill.expansion",
        "council.councillor.responded": "council.response",
        "conversation.message.added": "conversation.message",
        "artifact.read": "artifact.payload",
        "artifact.stored": "artifact.payload",
    }
    return mapping.get(event_type, event_type)


class EventBus:
    def __init__(
        self,
        sinks: list[EventSink] | None = None,
        enabled: bool = True,
        redact_on_emit: bool = False,
        blob_sink: BlobSink | None = None,
        blob_inline_threshold_bytes: int = 4096,
    ) -> None:
        self._sinks = sinks if sinks is not None else [NoopEventSink()]
        self._enabled = enabled
        self._redact_on_emit = redact_on_emit
        self._blob_sink = blob_sink
        self._blob_threshold = max(0, int(blob_inline_threshold_bytes))
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

    # ── Internal helpers ──────────────────────────────────────────────

    def _maybe_page_content(self, event: RuntimeEvent) -> RuntimeEvent:
        """If content is large and a blob sink is configured, write it out."""
        if not event.content or self._blob_sink is None:
            return event
        try:
            size = len(json.dumps(event.content, ensure_ascii=False, default=str))
        except Exception:
            return event
        if size <= self._blob_threshold:
            return event
        try:
            kind = _infer_content_kind(event.event_type)
            ref = self._blob_sink.write(event.event_id, event.ts, kind, event.content)
        except Exception:
            return event
        return replace(event, content={}, raw_payload_ref=ref)

    # ── Emission ──────────────────────────────────────────────────────

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

        # Inject the model_run_id once per emit, so call sites don't need to
        # remember to thread it through. Replay sessions set this via
        # set_model_run_id; in normal sessions it stays None.
        from runtime.events.runtime import get_model_run_id
        mrid = get_model_run_id()
        if mrid and event.model_run_id is None:
            event = replace(event, model_run_id=mrid)

        # 0090c — stamp the active scope tag so downstream analyses can group
        # by agent tier (main / runtime / subagent:<name>) without traversing
        # parent linkage. Call sites that need a different scope set it
        # explicitly; auto-population only fills in unset values.
        if event.agent_scope is None:
            try:
                from runtime.scope import current_scope
                event = replace(event, agent_scope=current_scope())
            except Exception:
                pass

        if self._redact_on_emit:
            from runtime.events.redactor import get_redactor
            event = get_redactor().redact_event(event)

        # Page large content AFTER redaction so secrets never reach disk.
        event = self._maybe_page_content(event)

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
