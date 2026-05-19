"""Unit tests for the replay engine.

End-to-end coverage:
  - loader parses a synthetic events.jsonl correctly
  - ReplayProvider serves recorded responses in FIFO order
  - ReplayingToolRegistry returns recorded outputs (both modes)
  - normalize_event strips volatile fields
  - diff_event_logs detects divergence and finds the first one
  - ReplayDivergenceError raised when streams desync

For the full "record then replay with real Gemini" loop see
tests/integration/test_replay_acceptance.py.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest

from arc.replay import (
    MissingRecordingError,
    ReplayDivergenceError,
    ReplayProvider,
    ReplayingToolRegistry,
    diff_event_logs,
    load,
    normalize_event,
)
from arc.runtime.hooks import ContentBlock, LLMRequest, LLMResponse


# ── Helpers ────────────────────────────────────────────────────────────────


def _write_recorded_session(
    session_dir: Path,
    events: list[dict],
    *,
    snapshot: str = "# fake snapshot\nprovider:\n  name: gemini\n",
) -> None:
    """Write a minimal recorded session to disk."""
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e, separators=(",", ":")) for e in events) + "\n",
        encoding="utf-8",
    )
    (session_dir / "meta.json").write_text(
        json.dumps({"session_id": session_dir.name}), encoding="utf-8",
    )
    (session_dir / "config.snapshot.yml").write_text(snapshot, encoding="utf-8")


def _evt(t, payload=None, content=None, parent=None):
    """Build a minimal event dict."""
    return {
        "event_id": f"EVT_{t}_{id(payload)}",  # unique enough for fixtures
        "session_id": "SES_FIXTURE",
        "turn_id": "TRN_FIXTURE",
        "scope": "main",
        "parent_event_id": parent,
        "ts": "2026-05-18T12:00:00",
        "ts_monotonic_ns": 0,
        "type": t,
        "stage": "test",
        "severity": "info",
        "duration_ms": None,
        "payload": payload or {},
        "content": content or {},
        "schema_version": 1,
    }


# ── Loader ─────────────────────────────────────────────────────────────────


def test_loader_missing_dir_raises(tmp_path):
    with pytest.raises(MissingRecordingError, match="not a directory"):
        load(tmp_path / "nope")


def test_loader_missing_events_raises(tmp_path):
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    (session_dir / "config.snapshot.yml").write_text("")
    with pytest.raises(MissingRecordingError, match="missing events.jsonl"):
        load(session_dir)


def test_loader_missing_snapshot_raises(tmp_path):
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    (session_dir / "events.jsonl").write_text("")
    with pytest.raises(MissingRecordingError, match="missing config.snapshot"):
        load(session_dir)


def test_loader_extracts_user_inputs(tmp_path):
    session_dir = tmp_path / "SES_test"
    events = [
        _evt("session.started"),
        _evt("turn.started", content={"user_input": "hello world"}),
        _evt("turn.ended"),
        _evt("turn.started", content={"user_input": "second turn"}),
        _evt("turn.ended"),
        _evt("session.ended"),
    ]
    _write_recorded_session(session_dir, events)

    data = load(session_dir)
    assert data.user_inputs == ["hello world", "second turn"]


def test_loader_extracts_llm_responses(tmp_path):
    session_dir = tmp_path / "SES_test"
    events = [
        _evt("session.started"),
        _evt("turn.started", content={"user_input": "x"}),
        _evt("llm.call.started"),
        _evt(
            "llm.call.completed",
            payload={"stop_reason": "end_turn", "input_tokens": 5, "output_tokens": 3},
            content={
                "response_content": [{"type": "text", "text": "reply A"}],
                "raw_provider_response": {"meta": "data"},
            },
        ),
        _evt("turn.ended"),
        _evt("session.ended"),
    ]
    _write_recorded_session(session_dir, events)

    data = load(session_dir)
    assert len(data.llm_responses) == 1
    resp = data.llm_responses[0]
    assert resp.stop_reason == "end_turn"
    assert resp.input_tokens == 5
    assert resp.output_tokens == 3
    assert len(resp.content) == 1
    assert resp.content[0].type == "text"
    assert resp.content[0].text == "reply A"
    assert resp.raw == {"meta": "data"}


def test_loader_extracts_tool_outputs_in_order_and_by_call(tmp_path):
    session_dir = tmp_path / "SES_test"
    events = [
        _evt("session.started"),
        _evt("turn.started", content={"user_input": "x"}),
        _evt("tool.call.started",
             payload={"tool_name": "ls", "tool_call_id": "tcl_1"},
             content={"input": {"path": "/tmp"}}),
        _evt("tool.call.completed",
             payload={"tool_name": "ls", "tool_call_id": "tcl_1", "ok": True},
             content={"output": "a.txt\nb.txt"}),
        _evt("tool.call.started",
             payload={"tool_name": "ls", "tool_call_id": "tcl_2"},
             content={"input": {"path": "/other"}}),
        _evt("tool.call.completed",
             payload={"tool_name": "ls", "tool_call_id": "tcl_2", "ok": True},
             content={"output": "c.txt"}),
        _evt("turn.ended"),
    ]
    _write_recorded_session(session_dir, events)

    data = load(session_dir)
    # in_order: two outputs queued for ls
    assert list(data.tool_outputs_in_order["ls"]) == ["a.txt\nb.txt", "c.txt"]
    # by_call: keyed by (name, canonical_input)
    key1 = ("ls", '{"path":"/tmp"}')
    key2 = ("ls", '{"path":"/other"}')
    assert list(data.tool_outputs_by_call[key1]) == ["a.txt\nb.txt"]
    assert list(data.tool_outputs_by_call[key2]) == ["c.txt"]


# ── ReplayProvider ─────────────────────────────────────────────────────────


def _resp(text="ok"):
    return LLMResponse(
        content=[ContentBlock(type="text", text=text)],
        stop_reason="end_turn",
        input_tokens=1, output_tokens=1, raw={},
    )


def _req():
    return LLMRequest(messages=[], system="", tools=[], model="x", params={})


def test_replay_provider_serves_in_order():
    p = ReplayProvider(deque([_resp("first"), _resp("second")]))
    assert p.chat(_req()).content[0].text == "first"
    assert p.chat(_req()).content[0].text == "second"
    assert p.remaining == 0


def test_replay_provider_raises_when_queue_empty():
    p = ReplayProvider(deque([_resp("only")]))
    p.chat(_req())  # consumes the one response
    with pytest.raises(ReplayDivergenceError, match="LLM call #2"):
        p.chat(_req())


# ── ReplayingToolRegistry — mode 2 (in_order) ─────────────────────────────


def test_replaying_tools_in_order_returns_recorded(tmp_path):
    session_dir = tmp_path / "SES"
    events = [
        _evt("tool.call.started", payload={"tool_name": "ls", "tool_call_id": "1"},
             content={"input": {"path": "/a"}}),
        _evt("tool.call.completed", payload={"tool_name": "ls", "tool_call_id": "1", "ok": True},
             content={"output": "OUTPUT-1"}),
        _evt("tool.call.started", payload={"tool_name": "ls", "tool_call_id": "2"},
             content={"input": {"path": "/b"}}),
        _evt("tool.call.completed", payload={"tool_name": "ls", "tool_call_id": "2", "ok": True},
             content={"output": "OUTPUT-2"}),
    ]
    _write_recorded_session(session_dir, events)
    data = load(session_dir)

    reg = ReplayingToolRegistry(data, mode="in_order")
    tool = reg.get("ls")
    # Inputs are ignored in in_order mode; just dequeue
    assert tool.execute({"anything": "goes"}) == "OUTPUT-1"
    assert tool.execute({}) == "OUTPUT-2"


def test_replaying_tools_in_order_diverges_when_empty(tmp_path):
    session_dir = tmp_path / "SES"
    _write_recorded_session(session_dir, [
        _evt("tool.call.started", payload={"tool_name": "ls", "tool_call_id": "1"},
             content={"input": {}}),
        _evt("tool.call.completed", payload={"tool_name": "ls", "tool_call_id": "1", "ok": True},
             content={"output": "only one"}),
    ])
    data = load(session_dir)
    reg = ReplayingToolRegistry(data, mode="in_order")
    tool = reg.get("ls")
    tool.execute({})  # consumes the one
    with pytest.raises(ReplayDivergenceError, match="more times than the recording"):
        tool.execute({})


# ── ReplayingToolRegistry — mode 3 (by_call) ──────────────────────────────


def test_replaying_tools_by_call_returns_match(tmp_path):
    session_dir = tmp_path / "SES"
    _write_recorded_session(session_dir, [
        _evt("tool.call.started", payload={"tool_name": "ls", "tool_call_id": "1"},
             content={"input": {"path": "/tmp"}}),
        _evt("tool.call.completed", payload={"tool_name": "ls", "tool_call_id": "1", "ok": True},
             content={"output": "tmp-contents"}),
    ])
    data = load(session_dir)
    reg = ReplayingToolRegistry(data, mode="by_call")
    assert reg.get("ls").execute({"path": "/tmp"}) == "tmp-contents"


def test_replaying_tools_by_call_diverges_on_unknown_input(tmp_path):
    session_dir = tmp_path / "SES"
    _write_recorded_session(session_dir, [
        _evt("tool.call.started", payload={"tool_name": "ls", "tool_call_id": "1"},
             content={"input": {"path": "/tmp"}}),
        _evt("tool.call.completed", payload={"tool_name": "ls", "tool_call_id": "1", "ok": True},
             content={"output": "tmp-contents"}),
    ])
    data = load(session_dir)
    reg = ReplayingToolRegistry(data, mode="by_call")
    with pytest.raises(ReplayDivergenceError, match="recording doesn't cover"):
        reg.get("ls").execute({"path": "/elsewhere"})


def test_replaying_tools_by_call_canonicalizes_input_order(tmp_path):
    """Key order in tool input shouldn't matter — lookups are by sorted keys."""
    session_dir = tmp_path / "SES"
    _write_recorded_session(session_dir, [
        _evt("tool.call.started", payload={"tool_name": "tool", "tool_call_id": "1"},
             content={"input": {"a": 1, "b": 2}}),
        _evt("tool.call.completed", payload={"tool_name": "tool", "tool_call_id": "1", "ok": True},
             content={"output": "matched"}),
    ])
    data = load(session_dir)
    reg = ReplayingToolRegistry(data, mode="by_call")
    # Same dict, different declaration order — should still match
    assert reg.get("tool").execute({"b": 2, "a": 1}) == "matched"


# ── Diff / normalization ──────────────────────────────────────────────────


def test_normalize_strips_event_id():
    e = _evt("turn.started")
    n = normalize_event(e, 0)
    assert n["event_id"] == "EVT_REPLAY_PLACEHOLDER"
    assert n["ts"] == ""
    assert n["ts_monotonic_ns"] == ""


def test_normalize_preserves_payload_and_content():
    e = _evt("llm.call.completed",
             payload={"stop_reason": "end_turn", "input_tokens": 5},
             content={"response_content": [{"type": "text", "text": "hi"}]})
    n = normalize_event(e, 0)
    assert n["payload"]["stop_reason"] == "end_turn"
    assert n["payload"]["input_tokens"] == 5
    assert n["content"]["response_content"][0]["text"] == "hi"


def test_normalize_strips_tool_call_id_from_payload():
    e = _evt("tool.call.completed",
             payload={"tool_name": "ls", "tool_call_id": "tcl_abc", "ok": True})
    n = normalize_event(e, 0)
    assert n["payload"]["tool_call_id"] == "TCL_REPLAY_PLACEHOLDER"
    assert n["payload"]["tool_name"] == "ls"
    assert n["payload"]["ok"] is True


def test_normalize_treats_raw_provider_response_as_opaque():
    """Provider-internal IDs/timestamps must not cause false diff."""
    e = _evt("llm.call.completed",
             content={"raw_provider_response": {"foo": "bar", "id": "varies"}})
    n = normalize_event(e, 0)
    assert n["content"]["raw_provider_response"] == {"__opaque_provider_response__": True}


def test_diff_matches_identical_logs(tmp_path):
    events = [_evt("session.started"), _evt("turn.started")]
    a_path = tmp_path / "a.jsonl"
    b_path = tmp_path / "b.jsonl"
    for p in (a_path, b_path):
        p.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    result = diff_event_logs(a_path, b_path)
    assert result.matched
    assert result.first_divergence_index is None
    assert result.n_events_a == result.n_events_b == 2


def test_diff_finds_first_divergence_point(tmp_path):
    a_path = tmp_path / "a.jsonl"
    b_path = tmp_path / "b.jsonl"
    a = [_evt("session.started"), _evt("turn.started"), _evt("turn.ended")]
    b = [_evt("session.started"), _evt("turn.started"),
         _evt("llm.call.failed", payload={"unexpected": True}), _evt("turn.ended")]
    a_path.write_text("\n".join(json.dumps(e) for e in a) + "\n")
    b_path.write_text("\n".join(json.dumps(e) for e in b) + "\n")
    result = diff_event_logs(a_path, b_path)
    assert not result.matched
    assert result.first_divergence_index == 2  # third event diverged
    assert "llm.call.failed" in result.unified_diff


def test_diff_detects_length_mismatch(tmp_path):
    a_path = tmp_path / "a.jsonl"
    b_path = tmp_path / "b.jsonl"
    a_path.write_text(json.dumps(_evt("turn.started")) + "\n")
    b_path.write_text(json.dumps(_evt("turn.started")) + "\n"
                      + json.dumps(_evt("turn.ended")) + "\n")
    result = diff_event_logs(a_path, b_path)
    assert not result.matched
    assert result.first_divergence_index == 1


def test_diff_ignores_volatile_fields(tmp_path):
    """Two logs with different event IDs + timestamps but same content match."""
    a_path = tmp_path / "a.jsonl"
    b_path = tmp_path / "b.jsonl"
    a = _evt("turn.started", content={"user_input": "same"})
    b = dict(a)
    b["event_id"] = "EVT_DIFFERENT"
    b["ts"] = "2099-01-01T00:00:00"
    b["ts_monotonic_ns"] = 999999
    a_path.write_text(json.dumps(a) + "\n")
    b_path.write_text(json.dumps(b) + "\n")
    result = diff_event_logs(a_path, b_path)
    assert result.matched
