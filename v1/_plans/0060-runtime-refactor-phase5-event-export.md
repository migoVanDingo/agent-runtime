# 0060 - Runtime Refactor Phase 5: Event Dataset Export

## Goal

Make the new structured event sidecar immediately useful for aggregate analysis without parsing human logs.

## Implemented

- Added `scripts/export_events.py`.
- Reads `_events/**/*.jsonl`.
- Flattens common runtime fields and payload fields into CSV.
- Writes `_events/events.csv` by default.

Flattened fields:

- timestamp
- event type
- session id
- turn id
- pipeline run id
- stage
- tool name
- policy decision
- ok/error status
- error code
- result byte count

## Usage

```bash
python3 scripts/export_events.py
python3 scripts/export_events.py --events-dir _events --out /tmp/events.csv
```

## Behavior Notes

This is intentionally small and dependency-free. It gives us a dataset path now, and it can later be extended to DuckDB or Parquet when dependencies and schema stabilize.

## Remaining Work

- Add richer event schemas for stage/provider/sandbox events.
- Add event redaction and privacy classification enforcement.
- Add session/turn identity propagation through `PipelineContext`.
- Add a DuckDB/Parquet export mode.

## Verification

Run:

```bash
python3 scripts/export_events.py --events-dir _events --out /tmp/events.csv
```

If there are no event files yet, the script writes a header-only CSV.
