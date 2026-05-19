"""Unit tests for branch (resume --at-turn) + rerun (mode 5).

Both rely on event-walking. Tests cover the extraction logic with
synthetic event lists; integration tests cover the CLI end-to-end.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from arc.rerun import user_inputs_from_session
from arc.resume import (
    count_completed_turns,
    messages_from_events,
    messages_from_session,
)
from arc.runtime.events import EventType


def _evt(t, payload=None, content=None):
    return {
        "event_id": f"EVT_{t}", "session_id": "SES_x", "turn_id": "TRN_x",
        "scope": "main", "parent_event_id": None,
        "ts": "2026-05-19T00:00:00", "ts_monotonic_ns": 0,
        "type": t, "stage": "test", "severity": "info", "duration_ms": None,
        "payload": payload or {}, "content": content or {}, "schema_version": 1,
    }


def _multi_turn_events():
    """Three completed turns."""
    return [
        _evt(EventType.SESSION_STARTED),
        # Turn 1
        _evt(EventType.TURN_STARTED, content={"user_input": "turn 1 question"}),
        _evt(EventType.LLM_CALL_COMPLETED,
             content={"response_content": [{"type": "text", "text": "turn 1 answer"}]}),
        _evt(EventType.TURN_ENDED),
        # Turn 2
        _evt(EventType.TURN_STARTED, content={"user_input": "turn 2 question"}),
        _evt(EventType.LLM_CALL_COMPLETED,
             content={"response_content": [{"type": "text", "text": "turn 2 answer"}]}),
        _evt(EventType.TURN_ENDED),
        # Turn 3
        _evt(EventType.TURN_STARTED, content={"user_input": "turn 3 question"}),
        _evt(EventType.LLM_CALL_COMPLETED,
             content={"response_content": [{"type": "text", "text": "turn 3 answer"}]}),
        _evt(EventType.TURN_ENDED),
        _evt(EventType.SESSION_ENDED),
    ]


def _write_session(tmp_path: Path, events: list[dict]) -> Path:
    session_dir = tmp_path / "SES_test"
    session_dir.mkdir()
    (session_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n"
    )
    return session_dir


# ── messages_from_events with max_turns ────────────────────────────────


def test_max_turns_none_returns_all_messages():
    msgs = messages_from_events(_multi_turn_events())
    assert len(msgs) == 6  # 3 user + 3 assistant


def test_max_turns_1_returns_first_turn_only():
    msgs = messages_from_events(_multi_turn_events(), max_turns=1)
    assert len(msgs) == 2  # 1 user + 1 assistant
    assert msgs[0].content == "turn 1 question"
    assert msgs[1].content[0].text == "turn 1 answer"


def test_max_turns_2_returns_first_two_turns():
    msgs = messages_from_events(_multi_turn_events(), max_turns=2)
    assert len(msgs) == 4
    assert msgs[2].content == "turn 2 question"
    assert msgs[3].content[0].text == "turn 2 answer"


def test_max_turns_0_returns_empty_list():
    msgs = messages_from_events(_multi_turn_events(), max_turns=0)
    assert msgs == []


def test_max_turns_higher_than_available_returns_everything():
    msgs = messages_from_events(_multi_turn_events(), max_turns=99)
    assert len(msgs) == 6


def test_max_turns_includes_tool_messages_within_turn():
    """A turn with a tool call: messages include user + assistant(tool_use) + tool + assistant(text)."""
    events = [
        _evt(EventType.SESSION_STARTED),
        _evt(EventType.TURN_STARTED, content={"user_input": "list"}),
        _evt(EventType.LLM_CALL_COMPLETED, content={"response_content": [
            {"type": "tool_use", "tool_use_id": "t1",
             "tool_name": "ls", "tool_input": {}},
        ]}),
        _evt(EventType.TOOL_CALL_COMPLETED,
             payload={"tool_name": "ls", "tool_call_id": "t1"},
             content={"output": "a\nb"}),
        _evt(EventType.LLM_CALL_COMPLETED,
             content={"response_content": [{"type": "text", "text": "done"}]}),
        _evt(EventType.TURN_ENDED),
        # turn 2 - should NOT be included when max_turns=1
        _evt(EventType.TURN_STARTED, content={"user_input": "next"}),
        _evt(EventType.LLM_CALL_COMPLETED,
             content={"response_content": [{"type": "text", "text": "next reply"}]}),
        _evt(EventType.TURN_ENDED),
    ]
    msgs = messages_from_events(events, max_turns=1)
    # user + assistant(tool_use) + tool + assistant(text) = 4
    assert len(msgs) == 4
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]


# ── count_completed_turns ─────────────────────────────────────────────


def test_count_completed_turns_matches_recording(tmp_path):
    session_dir = _write_session(tmp_path, _multi_turn_events())
    assert count_completed_turns(session_dir) == 3


def test_count_completed_turns_returns_0_for_missing_events(tmp_path):
    assert count_completed_turns(tmp_path / "missing") == 0


def test_count_completed_turns_handles_partial_recording(tmp_path):
    events = _multi_turn_events()[:5]  # cut mid-turn-2
    session_dir = _write_session(tmp_path, events)
    assert count_completed_turns(session_dir) == 1  # only turn 1 ended


# ── messages_from_session with max_turns ─────────────────────────────


def test_messages_from_session_threads_max_turns(tmp_path):
    session_dir = _write_session(tmp_path, _multi_turn_events())
    full = messages_from_session(session_dir)
    branched = messages_from_session(session_dir, max_turns=1)
    assert len(full) == 6
    assert len(branched) == 2


# ── user_inputs_from_session ─────────────────────────────────────────


def test_user_inputs_extracted_in_turn_order(tmp_path):
    session_dir = _write_session(tmp_path, _multi_turn_events())
    inputs = user_inputs_from_session(session_dir)
    assert inputs == [
        "turn 1 question",
        "turn 2 question",
        "turn 3 question",
    ]


def test_user_inputs_empty_when_no_turns(tmp_path):
    session_dir = _write_session(tmp_path, [_evt(EventType.SESSION_STARTED),
                                             _evt(EventType.SESSION_ENDED)])
    assert user_inputs_from_session(session_dir) == []


def test_user_inputs_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        user_inputs_from_session(tmp_path / "nope")


def test_user_inputs_skip_malformed_lines(tmp_path):
    """Resilient parsing — bad JSON line doesn't kill the extract."""
    session_dir = tmp_path / "SES"
    session_dir.mkdir()
    valid = _evt(EventType.TURN_STARTED, content={"user_input": "real"})
    (session_dir / "events.jsonl").write_text(
        json.dumps(valid) + "\n"
        "{not valid json\n"
        + json.dumps(_evt(EventType.TURN_STARTED, content={"user_input": "also real"})) + "\n"
    )
    assert user_inputs_from_session(session_dir) == ["real", "also real"]
