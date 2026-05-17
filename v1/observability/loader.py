"""Structured event loader — reads JSONL event files into pandas DataFrames.

Usage:
    from observability.loader import load_session, tool_calls_for, llm_calls_for

    df = load_session("SES01ABC...", events_dir=Path("_events"))
    calls = tool_calls_for("SES01ABC...", events_dir=Path("_events"))
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_DEFAULT_EVENTS_DIR = Path("_events")


def _iter_events(session_id: str, events_dir: Path) -> Iterator[dict]:
    path = events_dir / f"{session_id}.jsonl"
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _flatten(event: dict) -> dict:
    """Flatten an event dict into a single-level row."""
    payload = event.get("payload") or {}
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
        "privacy_class": (event.get("privacy") or {}).get("classification"),
        "redacted": (event.get("privacy") or {}).get("redacted"),
    }
    # Merge flat payload fields
    for k, v in payload.items():
        row[f"payload_{k}"] = v
    return row


def load_session(
    session_id: str,
    events_dir: Path = _DEFAULT_EVENTS_DIR,
) -> "pd.DataFrame":
    """Load all events for a session into a DataFrame."""
    import pandas as pd
    rows = [_flatten(e) for e in _iter_events(session_id, events_dir)]
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def load_sessions(
    events_dir: Path = _DEFAULT_EVENTS_DIR,
    since: datetime | None = None,
    until: datetime | None = None,
) -> "pd.DataFrame":
    """Load events from all sessions in events_dir, optionally filtered by time."""
    import pandas as pd

    all_rows: list[dict] = []
    if not events_dir.exists():
        return pd.DataFrame()

    for path in sorted(events_dir.glob("*.jsonl")):
        session_id = path.stem
        for event in _iter_events(session_id, events_dir):
            row = _flatten(event)
            if since is not None or until is not None:
                ts_str = event.get("ts", "")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if since and ts < since:
                            continue
                        if until and ts > until:
                            continue
                    except ValueError:
                        pass
            all_rows.append(row)

    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()


def tool_calls_for(
    session_id: str,
    events_dir: Path = _DEFAULT_EVENTS_DIR,
) -> "pd.DataFrame":
    """Return only tool.call.completed events for a session."""
    df = load_session(session_id, events_dir)
    if df.empty:
        return df
    return df[df["event_type"] == "tool.call.completed"].reset_index(drop=True)


def llm_calls_for(
    session_id: str,
    events_dir: Path = _DEFAULT_EVENTS_DIR,
) -> "pd.DataFrame":
    """Return only llm.call.completed events for a session."""
    df = load_session(session_id, events_dir)
    if df.empty:
        return df
    return df[df["event_type"] == "llm.call.completed"].reset_index(drop=True)


def joined_session_summary(
    session_id: str,
    events_dir: Path = _DEFAULT_EVENTS_DIR,
) -> "pd.DataFrame":
    """Return a summary row per turn: start/end times, tool calls, LLM calls."""
    import pandas as pd

    df = load_session(session_id, events_dir)
    if df.empty:
        return pd.DataFrame()

    turns = df[df["event_type"] == "turn.started"][["turn_id", "ts"]].rename(
        columns={"ts": "turn_started_at"}
    )
    ends = df[df["event_type"] == "turn.completed"][["turn_id", "ts"]].rename(
        columns={"ts": "turn_ended_at"}
    )
    tool_counts = (
        df[df["event_type"] == "tool.call.completed"]
        .groupby("turn_id")
        .size()
        .reset_index(name="tool_calls")
    )
    llm_counts = (
        df[df["event_type"] == "llm.call.completed"]
        .groupby("turn_id")
        .size()
        .reset_index(name="llm_calls")
    )

    result = turns.merge(ends, on="turn_id", how="left")
    result = result.merge(tool_counts, on="turn_id", how="left")
    result = result.merge(llm_counts, on="turn_id", how="left")
    result[["tool_calls", "llm_calls"]] = result[["tool_calls", "llm_calls"]].fillna(0).astype(int)
    return result
