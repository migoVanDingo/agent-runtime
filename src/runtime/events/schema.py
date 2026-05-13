"""Structured event schema for machine-readable runtime telemetry.

Schema v2.0 — see _plans/0087-telemetry-overhaul.md.

Compared to v1.0:
- Top-level metric fields (duration_ms, input_tokens, output_tokens, cost_usd, ...)
  so a pandas analyst can use them directly without json_normalize.
- Top-level model identity (provider, model, temperature, ...).
- A separate ``content`` dict for large payloads (full prompts, tool I/O, plan
  JSON). Above a configurable threshold the bus pages content to a blob file
  and sets ``raw_payload_ref``.
- ``severity`` and ``event_family`` for classification.
- ``model_run_id`` for cross-session replay correlation.

All new fields are optional and default to None / empty. Existing call sites
that only pass event_type/identity/payload remain valid.

v1 logs (schema_version == "1.0") remain readable via ``legacy_v1_to_v2_view``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from runtime.identity import RuntimeIdentity, new_id

SCHEMA_VERSION = "2.0"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_family(event_type: str) -> str:
    return event_type.split(".", 1)[0] if event_type else ""


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
    content: dict[str, Any] = field(default_factory=dict)
    stage: str | None = None
    parent_event_id: str | None = None
    privacy: EventPrivacy = field(default_factory=EventPrivacy)
    event_id: str = field(default_factory=lambda: new_id("EVT"))
    ts: str = field(default_factory=utc_now_iso)
    schema_version: str = SCHEMA_VERSION
    severity: str = "info"

    # ── Metrics (only set when the event has them) ────────────────────
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_input_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cost_usd: float | None = None

    # ── Model identification (LLM events) ─────────────────────────────
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stop_reason: str | None = None
    finish_reason_normalized: str | None = None

    # ── Replay correlation ────────────────────────────────────────────
    model_run_id: str | None = None

    # ── Blob paging ───────────────────────────────────────────────────
    raw_payload_ref: str | None = None
    redacted: bool = False

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "event_family": _derive_family(self.event_type),
            "ts": self.ts,
            "parent_event_id": self.parent_event_id,
            "stage": self.stage,
            "severity": self.severity,
            "privacy": self.privacy.to_dict(),
            "payload": self.payload,
        }
        # Content + blob ref — only included when populated, to keep JSONL slim.
        if self.content:
            data["content"] = self.content
        if self.raw_payload_ref is not None:
            data["raw_payload_ref"] = self.raw_payload_ref
        if self.redacted:
            data["redacted"] = True

        # Flatten identity (session_id, turn_id, …).
        data.update(self.identity.to_event_fields())

        # Flatten metrics + model identity. None values are written so analysts
        # see a consistent column set; pandas treats them as NaN.
        for key in (
            "duration_ms",
            "input_tokens",
            "output_tokens",
            "cache_input_tokens",
            "cache_creation_tokens",
            "cost_usd",
            "provider",
            "model",
            "temperature",
            "max_tokens",
            "stop_reason",
            "finish_reason_normalized",
            "model_run_id",
        ):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data


def legacy_v1_to_v2_view(record: dict[str, Any]) -> dict[str, Any]:
    """Promote v1 payload fields to top-level v2 fields in a read-time view.

    Does not mutate the input. Used by analysts loading historical (v1) JSONL
    files into a v2-shaped DataFrame.
    """
    if record.get("schema_version") != "1.0":
        return record
    out = dict(record)
    payload = record.get("payload") or {}

    # LLM events: lift the metric fields that v1 buried.
    promotions = {
        "tokens_in": "input_tokens",
        "input_tokens": "input_tokens",
        "tokens_out": "output_tokens",
        "output_tokens": "output_tokens",
        "latency_ms": "duration_ms",
        "duration_ms": "duration_ms",
        "provider": "provider",
        "model": "model",
        "stop_reason": "stop_reason",
    }
    for src, dst in promotions.items():
        if src in payload and dst not in out:
            out[dst] = payload[src]

    out["event_family"] = _derive_family(record.get("event_type", ""))
    out.setdefault("severity", "info")
    return out
