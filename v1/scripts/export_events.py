#!/usr/bin/env python3
"""Aggregate structured runtime events.

Reads JSONL files from _events/ and writes a compact CSV summary. This is the
first dataset utility: it avoids scraping human-readable logs while keeping the
output easy to inspect with spreadsheets, DuckDB, or pandas.

Usage:
    python3 scripts/export_events.py
    python3 scripts/export_events.py --events-dir _events --out /tmp/events.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


FIELDS = [
    "ts",
    "event_type",
    "session_id",
    "turn_id",
    "pipeline_run_id",
    "stage",
    "tool_name",
    "decision",
    "ok",
    "error_code",
    "result_bytes",
]


def iter_events(root: Path):
    if not root.exists():
        return
    for path in sorted(root.rglob("*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    yield {
                        "ts": "",
                        "event_type": "event.parse_error",
                        "session_id": path.stem,
                        "payload": {"error": str(e), "file": str(path), "line": line_no},
                    }


def _redact_payload(payload: dict) -> dict:
    """Apply redaction to a payload dict using the default regex redactor."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    try:
        from runtime.events.redactor import get_redactor
        return get_redactor().redact_payload(payload)
    except Exception:
        return payload


def flatten(event: dict[str, Any]) -> dict[str, Any]:
    payload = _redact_payload(event.get("payload") or {})
    return {
        "ts": event.get("ts", ""),
        "event_type": event.get("event_type", ""),
        "session_id": event.get("session_id", ""),
        "turn_id": event.get("turn_id", ""),
        "pipeline_run_id": event.get("pipeline_run_id", ""),
        "stage": event.get("stage", ""),
        "tool_name": payload.get("tool_name", ""),
        "decision": payload.get("decision", ""),
        "ok": payload.get("ok", ""),
        "error_code": payload.get("error_code", ""),
        "result_bytes": payload.get("result_bytes", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export runtime events to CSV")
    parser.add_argument("--events-dir", default="_events")
    parser.add_argument("--out", default="_events/events.csv")
    args = parser.parse_args()

    root = Path(args.events_dir)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows = [flatten(e) for e in iter_events(root) or []]
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} event row(s) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
