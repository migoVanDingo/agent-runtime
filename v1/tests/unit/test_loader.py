"""Tests for observability/loader.py."""
import json
import pytest
import tempfile
from pathlib import Path

from observability.loader import load_session, tool_calls_for, llm_calls_for


def _write_events(events_dir: Path, session_id: str, events: list) -> None:
    path = events_dir / f"{session_id}.jsonl"
    with open(path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _evt(event_type: str, session_id: str = "SESS1", payload: dict | None = None) -> dict:
    return {
        "event_type": event_type,
        "session_id": session_id,
        "turn_id": "TURN1",
        "pipeline_run_id": None,
        "ts": "2026-05-03T12:00:00+00:00",
        "schema_version": "1.0",
        "stage": "test",
        "payload": payload or {},
        "privacy": {"classification": "internal", "redacted": True},
    }


def test_load_session_returns_dataframe():
    with tempfile.TemporaryDirectory() as d:
        events_dir = Path(d)
        _write_events(events_dir, "SESS1", [
            _evt("turn.started"),
            _evt("tool.call.completed", payload={"tool_name": "read_file", "ok": True, "result_bytes": 512}),
            _evt("turn.completed"),
        ])
        df = load_session("SESS1", events_dir)
        assert len(df) == 3
        assert set(df["event_type"]) == {"turn.started", "tool.call.completed", "turn.completed"}


def test_load_session_empty_for_missing_file():
    with tempfile.TemporaryDirectory() as d:
        df = load_session("NONEXISTENT", Path(d))
        assert df.empty


def test_tool_calls_for_filters_correctly():
    with tempfile.TemporaryDirectory() as d:
        events_dir = Path(d)
        _write_events(events_dir, "SESS1", [
            _evt("turn.started"),
            _evt("tool.call.completed", payload={"tool_name": "bash_exec", "ok": True, "result_bytes": 100}),
            _evt("llm.call.completed", payload={"model": "claude-3", "input_tokens": 500}),
            _evt("tool.call.completed", payload={"tool_name": "read_file", "ok": True, "result_bytes": 200}),
        ])
        tc = tool_calls_for("SESS1", events_dir)
        assert len(tc) == 2
        assert all(tc["event_type"] == "tool.call.completed")


def test_llm_calls_for_filters_correctly():
    with tempfile.TemporaryDirectory() as d:
        events_dir = Path(d)
        _write_events(events_dir, "SESS1", [
            _evt("llm.call.completed", payload={"model": "claude-3"}),
            _evt("tool.call.completed", payload={"tool_name": "x"}),
        ])
        lc = llm_calls_for("SESS1", events_dir)
        assert len(lc) == 1
        assert lc.iloc[0]["event_type"] == "llm.call.completed"


def test_payload_fields_are_flattened():
    with tempfile.TemporaryDirectory() as d:
        events_dir = Path(d)
        _write_events(events_dir, "SESS1", [
            _evt("tool.call.completed", payload={"tool_name": "read_file", "ok": True, "result_bytes": 42}),
        ])
        df = load_session("SESS1", events_dir)
        assert "payload_tool_name" in df.columns
        assert df.iloc[0]["payload_tool_name"] == "read_file"
        assert df.iloc[0]["payload_result_bytes"] == 42
