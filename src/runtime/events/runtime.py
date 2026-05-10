"""Process-level event bus wiring.

This keeps event emission optional and sidecar-only. Human-readable logging is
unchanged; structured events are emitted to configured sinks when enabled.
"""

from __future__ import annotations

from runtime.events.bus import EventBus, JsonlEventSink
from runtime.identity import RuntimeIdentity
from session_paths import events_dir

_event_bus: EventBus = EventBus.noop()
_identity: RuntimeIdentity | None = None


def init_runtime_events(session_id: str, *, project_id: str | None = None) -> EventBus:
    from app_config import config

    global _event_bus, _identity
    _identity = RuntimeIdentity.new_session(session_id=session_id, project_id=project_id)

    cfg = config.runtime.events
    if not cfg.enabled:
        _event_bus = EventBus.noop()
        return _event_bus

    sinks = []
    if cfg.jsonl_enabled:
        sinks.append(JsonlEventSink(events_dir(session_id) / "runtime.jsonl"))
    redact_on_emit = getattr(cfg, "redact_on_emit", False)
    _event_bus = EventBus(sinks or None, enabled=True, redact_on_emit=redact_on_emit)
    return _event_bus


def get_event_bus() -> EventBus:
    return _event_bus


def get_runtime_identity() -> RuntimeIdentity:
    if _identity is None:
        return RuntimeIdentity.new_session(session_id="SESSUNKNOWN")
    return _identity


def set_runtime_identity(identity: RuntimeIdentity) -> None:
    global _identity
    _identity = identity
