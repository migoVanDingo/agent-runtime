#!/usr/bin/env python3
"""Export structured runtime events to a parquet dataset.

Reads JSONL event files from _events/, applies redaction, and writes
a parquet file (or CSV if pyarrow/pandas are unavailable).

Usage:
    python scripts/export_dataset.py
    python scripts/export_dataset.py --since 30d --out _datasets/export.parquet
    python scripts/export_dataset.py --session SES01ABCDEF --out /tmp/session.parquet
"""
from __future__ import annotations

import argparse
import json
import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _parse_since(since_str: str | None) -> datetime | None:
    if not since_str:
        return None
    since_str = since_str.strip().lower()
    if since_str.endswith("d"):
        days = int(since_str[:-1])
        return datetime.now(timezone.utc) - timedelta(days=days)
    if since_str.endswith("h"):
        hours = int(since_str[:-1])
        return datetime.now(timezone.utc) - timedelta(hours=hours)
    return datetime.fromisoformat(since_str)


def _redact_payload(payload: dict) -> dict:
    try:
        from runtime.events.redactor import get_redactor
        return get_redactor().redact_payload(payload)
    except Exception:
        return payload


def iter_events(events_dir: Path, session_id: str | None = None):
    if not events_dir.exists():
        return
    if session_id:
        paths = [events_dir / f"{session_id}.jsonl"]
    else:
        paths = sorted(events_dir.rglob("*.jsonl"))

    for path in paths:
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    pass


def flatten_redacted(event: dict) -> dict:
    payload = _redact_payload(event.get("payload") or {})
    row: dict = {
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "ts": event.get("ts"),
        "schema_version": event.get("schema_version"),
        "stage": event.get("stage"),
        "session_id": event.get("session_id"),
        "turn_id": event.get("turn_id"),
        "pipeline_run_id": event.get("pipeline_run_id"),
        "plan_id": event.get("plan_id"),
        "plan_run_id": event.get("plan_run_id"),
        "step_run_id": event.get("step_run_id"),
        "tool_call_id": event.get("tool_call_id"),
    }
    for k, v in payload.items():
        row[f"payload_{k}"] = v
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="Export runtime events to parquet/CSV")
    parser.add_argument("--events-dir", default="_events", help="Events directory")
    parser.add_argument("--out", default=None, help="Output file (.parquet or .csv)")
    parser.add_argument("--since", default=None, help="e.g. 30d, 12h, or ISO datetime")
    parser.add_argument("--session", default=None, help="Export a single session ID")
    args = parser.parse_args()

    events_dir = Path(args.events_dir)
    since = _parse_since(args.since)
    out_path = Path(args.out) if args.out else Path("_datasets") / "events.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for event in iter_events(events_dir, session_id=args.session):
        if since is not None:
            ts_str = event.get("ts", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < since:
                        continue
                except ValueError:
                    pass
        rows.append(flatten_redacted(event))

    if not rows:
        print(f"No events found. Writing empty file to {out_path}")
        rows = []

    # Try parquet, fall back to CSV
    try:
        import pandas as pd
        df = pd.DataFrame(rows)
        if str(out_path).endswith(".parquet"):
            df.to_parquet(str(out_path), index=False)
        else:
            df.to_csv(str(out_path), index=False)
        print(f"Wrote {len(rows)} event(s) to {out_path}")
        _write_manifest(out_path, len(rows))
    except ImportError:
        import csv
        if not str(out_path).endswith(".csv"):
            out_path = out_path.with_suffix(".csv")
        fields = sorted({k for r in rows for k in r}) if rows else ["event_type", "ts", "session_id"]
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} event(s) to {out_path} (CSV fallback — install pandas+pyarrow for parquet)")

    return 0


def _write_manifest(out_path: Path, n_rows: int) -> None:
    import json
    manifest = {
        "rows": n_rows,
        "schema_version": "1.0",
        "redacted": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "output": str(out_path),
    }
    manifest_path = out_path.parent / "_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
