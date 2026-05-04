"""Event payload redactor.

Applied before export (always) and optionally at emit time when
`runtime.events.redact_on_emit` is true.

Redaction operates on the JSON-serialised payload string so it catches
secrets in any nested field without requiring schema knowledge.
"""
from __future__ import annotations

import json
import re
from typing import Any


# ── Rules ────────────────────────────────────────────────────────────────────
# Each rule is (pattern, replacement). Patterns are applied in order to the
# JSON-serialised payload string.

_RULES: list[tuple[re.Pattern, str]] = [
    # API key patterns
    (re.compile(r'(sk-[A-Za-z0-9]{10,})', re.ASCII), "<api_key>"),
    (re.compile(r'(ANTHROPIC_API_KEY\s*=\s*\S+)', re.IGNORECASE), "ANTHROPIC_API_KEY=<redacted>"),
    (re.compile(r'(OPENAI_API_KEY\s*=\s*\S+)', re.IGNORECASE), "OPENAI_API_KEY=<redacted>"),
    (re.compile(r'(BRAVE_API_KEY\s*=\s*\S+)', re.IGNORECASE), "BRAVE_API_KEY=<redacted>"),
    (re.compile(r'(GROK_API_KEY\s*=\s*\S+)', re.IGNORECASE), "GROK_API_KEY=<redacted>"),
    (re.compile(r'(DEEPSEEK_API_KEY\s*=\s*\S+)', re.IGNORECASE), "DEEPSEEK_API_KEY=<redacted>"),
    (re.compile(r'(GEMINI_API_KEY\s*=\s*\S+)', re.IGNORECASE), "GEMINI_API_KEY=<redacted>"),
    # Bearer tokens / JWTs
    (re.compile(r'(Bearer\s+[A-Za-z0-9\-._~+/]+=*)', re.IGNORECASE), "Bearer <token>"),
    (re.compile(r'([A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})'), "<jwt>"),
    # Home paths: /Users/alice/ or /home/alice/
    (re.compile(r'/(?:Users|home)/([^/\s"\']+)/'), r'/\1_home/'),
    # Email addresses
    (re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}'), "<email>"),
]


class RegexRedactor:
    """Applies regex rules to the JSON payload of a RuntimeEvent."""

    def __init__(self, extra_rules: list[tuple[re.Pattern, str]] | None = None) -> None:
        self._rules = list(_RULES) + (extra_rules or [])

    def redact_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a new payload dict with sensitive strings scrubbed."""
        try:
            serialised = json.dumps(payload, ensure_ascii=False)
        except Exception:
            return payload

        for pattern, replacement in self._rules:
            serialised = pattern.sub(replacement, serialised)

        try:
            return json.loads(serialised)
        except Exception:
            return payload

    def redact_event(self, event) -> "any":
        """Return a copy of the event with the payload redacted.

        Works with RuntimeEvent (frozen dataclass) by creating a new instance.
        """
        from runtime.events.schema import RuntimeEvent, EventPrivacy
        redacted_payload = self.redact_payload(event.payload)
        redacted_privacy = EventPrivacy(
            classification=event.privacy.classification,
            redacted=True,
            raw_content_stored=False,
        )
        return RuntimeEvent(
            event_type=event.event_type,
            identity=event.identity,
            payload=redacted_payload,
            stage=event.stage,
            parent_event_id=event.parent_event_id,
            privacy=redacted_privacy,
            event_id=event.event_id,
            ts=event.ts,
            schema_version=event.schema_version,
        )


_default_redactor: RegexRedactor | None = None


def get_redactor() -> RegexRedactor:
    global _default_redactor
    if _default_redactor is None:
        _default_redactor = RegexRedactor()
    return _default_redactor
