"""Event payload + content redactor.

Two-stage design:

- Stage 1 (always-on when ``redact_on_emit`` is true): scrub API keys, bearer
  tokens, JWTs, home paths, emails from the event payload AND the content
  dict before it reaches sinks / blob writes.
- Stage 2 (export-time, optional): stricter scrub for sharing — removes
  filenames, IP addresses, hostnames. Implemented in ``scripts/export_session.py``.

Redaction operates on the JSON-serialised representation so it catches secrets
in any nested field without requiring schema knowledge.
"""
from __future__ import annotations

import json
import re
from typing import Any


# ── Rules ────────────────────────────────────────────────────────────────────
# Each rule is (pattern, replacement). Patterns are applied in order.

_RULES: list[tuple[re.Pattern, str]] = [
    # API key patterns
    (re.compile(r'(sk-[A-Za-z0-9]{10,})', re.ASCII), "<api_key>"),
    (re.compile(r'(ANTHROPIC_API_KEY\s*=\s*[^\s"\',}]+)', re.IGNORECASE), "ANTHROPIC_API_KEY=<redacted>"),
    (re.compile(r'(OPENAI_API_KEY\s*=\s*[^\s"\',}]+)', re.IGNORECASE), "OPENAI_API_KEY=<redacted>"),
    (re.compile(r'(BRAVE_API_KEY\s*=\s*[^\s"\',}]+)', re.IGNORECASE), "BRAVE_API_KEY=<redacted>"),
    (re.compile(r'(GROK_API_KEY\s*=\s*[^\s"\',}]+)', re.IGNORECASE), "GROK_API_KEY=<redacted>"),
    (re.compile(r'(DEEPSEEK_API_KEY\s*=\s*[^\s"\',}]+)', re.IGNORECASE), "DEEPSEEK_API_KEY=<redacted>"),
    (re.compile(r'(GEMINI_API_KEY\s*=\s*[^\s"\',}]+)', re.IGNORECASE), "GEMINI_API_KEY=<redacted>"),
    # Bearer tokens / JWTs
    (re.compile(r'(Bearer\s+[A-Za-z0-9\-._~+/]+=*)', re.IGNORECASE), "Bearer <token>"),
    (re.compile(r'([A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})'), "<jwt>"),
    # Home paths: /Users/alice/ or /home/alice/
    (re.compile(r'/(?:Users|home)/([^/\s"\']+)/'), r'/\1_home/'),
    # Email addresses
    (re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}'), "<email>"),
]


class RegexRedactor:
    """Applies regex rules to event payload and content."""

    def __init__(self, extra_rules: list[tuple[re.Pattern, str]] | None = None) -> None:
        self._rules = list(_RULES) + (extra_rules or [])

    # ── Generic recursive scrub ────────────────────────────────────────

    def _scrub_json_str(self, serialised: str) -> str:
        for pattern, replacement in self._rules:
            serialised = pattern.sub(replacement, serialised)
        return serialised

    def _scrub_value(self, value: Any) -> Any:
        """Round-trip through JSON to scrub any string anywhere in the value."""
        try:
            s = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return value
        scrubbed = self._scrub_json_str(s)
        try:
            return json.loads(scrubbed)
        except Exception:
            return value

    # ── Public API ─────────────────────────────────────────────────────

    def redact_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a new payload dict with sensitive strings scrubbed."""
        out = self._scrub_value(payload)
        return out if isinstance(out, dict) else payload

    def redact_content(self, kind: str, content: Any) -> Any:
        """Return content with sensitive strings scrubbed.

        ``kind`` is the canonical content shape (e.g. ``"llm.prompt"``,
        ``"tool.output"``). Currently identical recursive scrub regardless of
        kind, but the parameter allows future per-kind rules (e.g. don't
        scrub the system-prompt body but still scrub ENV-like substrings).
        """
        del kind  # reserved for future per-kind rules
        return self._scrub_value(content)

    def redact_event(self, event) -> "any":
        """Return a copy of the event with payload + content redacted.

        Works with frozen RuntimeEvent dataclass via dataclasses.replace.
        """
        from dataclasses import replace
        from runtime.events.schema import EventPrivacy
        redacted_payload = self.redact_payload(event.payload)
        redacted_content = (
            self.redact_content(event.event_type, event.content) if event.content else {}
        )
        redacted_privacy = EventPrivacy(
            classification=event.privacy.classification,
            redacted=True,
            raw_content_stored=event.privacy.raw_content_stored,
        )
        return replace(
            event,
            payload=redacted_payload,
            content=redacted_content,
            privacy=redacted_privacy,
            redacted=True,
        )


_default_redactor: RegexRedactor | None = None


def get_redactor() -> RegexRedactor:
    global _default_redactor
    if _default_redactor is None:
        _default_redactor = RegexRedactor()
    return _default_redactor
