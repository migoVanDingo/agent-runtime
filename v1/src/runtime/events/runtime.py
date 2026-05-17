"""Process-level event bus wiring.

This keeps event emission optional and sidecar-only. Human-readable logging is
unchanged; structured events are emitted to configured sinks when enabled.
"""

from __future__ import annotations

from runtime.events.bus import BlobSink, EventBus, JsonlEventSink
from runtime.identity import RuntimeIdentity
from session_paths import events_dir

_event_bus: EventBus = EventBus.noop()
_identity: RuntimeIdentity | None = None
_model_run_id: str | None = None


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

    blob_sink = None
    if getattr(cfg, "blobs_enabled", True):
        blob_sink = BlobSink(events_dir(session_id) / "blobs")

    redact_on_emit = getattr(cfg, "redact_on_emit", False)
    blob_threshold = getattr(cfg, "blob_inline_threshold_bytes", 4096)
    _event_bus = EventBus(
        sinks or None,
        enabled=True,
        redact_on_emit=redact_on_emit,
        blob_sink=blob_sink,
        blob_inline_threshold_bytes=blob_threshold,
    )
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


def get_model_run_id() -> str | None:
    """Return the active model-run ID, set only during replay sessions."""
    return _model_run_id


def set_model_run_id(value: str | None) -> None:
    """Set the active model-run ID. Called once at the start of a replay."""
    global _model_run_id
    _model_run_id = value
