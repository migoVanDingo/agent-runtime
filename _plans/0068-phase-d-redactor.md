# 0068 — Phase D: Redactor + privacy enforcement

## Goal

Privacy classification on events is currently a constant label with no
scrubbing behind it. This phase adds a `RegexRedactor` that is applied
by the EventBus before export and optionally at emit time.

## Scope

- New `runtime/events/redactor.py`: `RegexRedactor` with default rules.
- `EventBus` accepts an optional `redactor` param; redacts before export.
- Config: `runtime.events.redact_on_emit` (default false), `runtime.events.redact_on_export` (default true).
- Export script applies redaction unconditionally.

## Default rules

- API key prefixes: `sk-`, `ANTHROPIC_API_KEY=`, `OPENAI_API_KEY=`, `BRAVE_API_KEY=`, etc.
- Home paths: `/Users/<name>/` → `~user/`, `/home/<name>/` → `~user/`.
- Email addresses → `<email>`.
- Bearer tokens, JWTs.

## Files touched

`runtime/events/redactor.py` (new), `runtime/events/bus.py`,
`runtime/events/runtime.py`, `config.py` (EventsConfig), `config.yml`,
`scripts/export_events.py`.

## Exit criteria

- Round-trip test: planted API key in event payload does not appear in
  redacted output.
- Tests for each default rule class.
