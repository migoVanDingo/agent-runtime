"""Structured runtime event primitives."""

from runtime.events.bus import EventBus, NoopEventSink
from runtime.events.redactor import RegexRedactor, get_redactor
from runtime.events.runtime import (
    get_event_bus,
    get_model_run_id,
    get_runtime_identity,
    init_runtime_events,
    set_model_run_id,
    set_runtime_identity,
)
from runtime.events.schema import (
    EventPrivacy,
    RuntimeEvent,
    SCHEMA_VERSION,
    legacy_v1_to_v2_view,
)

__all__ = [
    "EventBus",
    "NoopEventSink",
    "RuntimeEvent",
    "EventPrivacy",
    "SCHEMA_VERSION",
    "RegexRedactor",
    "get_redactor",
    "get_event_bus",
    "get_runtime_identity",
    "get_model_run_id",
    "set_model_run_id",
    "init_runtime_events",
    "set_runtime_identity",
    "legacy_v1_to_v2_view",
]
