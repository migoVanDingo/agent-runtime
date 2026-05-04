"""Structured runtime event primitives."""

from runtime.events.bus import EventBus, NoopEventSink
from runtime.events.redactor import RegexRedactor, get_redactor
from runtime.events.runtime import (
    get_event_bus,
    get_runtime_identity,
    init_runtime_events,
    set_runtime_identity,
)
from runtime.events.schema import RuntimeEvent, EventPrivacy

__all__ = [
    "EventBus",
    "NoopEventSink",
    "RuntimeEvent",
    "EventPrivacy",
    "RegexRedactor",
    "get_redactor",
    "get_event_bus",
    "get_runtime_identity",
    "init_runtime_events",
    "set_runtime_identity",
]
