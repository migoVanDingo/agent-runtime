"""Structured event schema for machine-readable runtime telemetry."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from runtime.identity import RuntimeIdentity, new_id

SCHEMA_VERSION = "1.0"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EventPrivacy:
    classification: str = "internal"
    redacted: bool = True
    raw_content_stored: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeEvent:
    event_type: str
    identity: RuntimeIdentity
    payload: dict[str, Any] = field(default_factory=dict)
    stage: str | None = None
    parent_event_id: str | None = None
    privacy: EventPrivacy = field(default_factory=EventPrivacy)
    event_id: str = field(default_factory=lambda: new_id("EVT"))
    ts: str = field(default_factory=utc_now_iso)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "ts": self.ts,
            "parent_event_id": self.parent_event_id,
            "stage": self.stage,
            "privacy": self.privacy.to_dict(),
            "payload": self.payload,
        }
        data.update(self.identity.to_event_fields())
        return data
