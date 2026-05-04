# 0059 - Runtime Refactor Phase 4: Structured Event Sidecar

## Goal

Keep the readable human logs intact while adding a machine-readable event stream for future analysis and replay.

## Implemented

- Enabled event config by default:
  - `runtime.events.enabled=true`
  - `runtime.events.jsonl_enabled=true`
- Added `_events` to `.gitignore`.
- Added process-level event runtime:
  - `init_runtime_events(session_id, project_id)`
  - `get_event_bus()`
  - `get_runtime_identity()`
- Wired `main.py` to emit:
  - `session.started`
  - `session.resumed`
  - `session.ended`
  - `turn.started`
  - `turn.completed`
  - `turn.failed`
- Wired `ToolCallExecutor` to emit:
  - `tool.call.started`
  - `policy.decision`
  - `tool.call.completed`
- Events write to `_events/<session_id>.jsonl` when enabled.
- Existing `_logs/*.log` human output remains unchanged.

## Behavior Notes

This phase intentionally stores previews rather than full raw content:

- user message preview,
- response preview,
- tool input preview,
- tool result preview.

That keeps event files useful for analysis without immediately turning them into a high-risk raw prompt/output dump.

## Remaining Work

- Propagate turn identity into the full `Agent.call()` and pipeline context rather than using process-level session identity for tool calls.
- Add stage-level events.
- Add provider token/cost events.
- Add export script to DuckDB/Parquet.
- Add redaction patterns for secrets.

## Verification

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src
```
