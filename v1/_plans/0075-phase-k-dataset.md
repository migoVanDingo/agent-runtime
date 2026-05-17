# 0075 — Phase K: Dataset loader + parquet export

## Goal

Make the structured event stream immediately useful for analysis. Provides
a pandas-based loader for session JSONL files and a parquet export script
that always applies redaction.

## Scope

- New `observability/__init__.py` — marks directory as a Python package.
- New `observability/loader.py`:
  - `load_session(session_id, events_dir)` → DataFrame
  - `load_sessions(events_dir, since, until)` → DataFrame
  - `tool_calls_for(session_id, events_dir)` → filtered DataFrame
  - `llm_calls_for(session_id, events_dir)` → filtered DataFrame
- Updated `scripts/export_dataset.py` — reads JSONL, applies redaction,
  writes parquet with DuckDB or fallback CSV.
- Updated `scripts/export_events.py` — updated to delegate to loader.

## Files touched

`observability/__init__.py` (new), `observability/loader.py` (new),
`scripts/export_dataset.py` (new).

## Exit criteria

- `loader.load_session(sid)` returns a non-empty DataFrame for a real session.
- `export_dataset.py --since 30d` runs without error.
- All tests pass.
