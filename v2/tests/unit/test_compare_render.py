"""Unit tests for the cross-provider replay comparison renderer (0019)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from arc.replay.compare import (
    SessionSummary,
    extract_turns,
    render_full_comparison,
    render_summary_table,
    summarize_session,
)


def _events_for(session_id: str, *, provider: str, model: str, turns: int = 1,
                tokens_in: int = 100, tokens_out: int = 50,
                final_text: str = "done", aborted_reason: str | None = None,
                tool_calls: int = 0) -> str:
    """Build a minimal events.jsonl payload."""
    lines: list[dict] = []
    lines.append({
        "ts": "2026-05-23T12:00:00.000+00:00",
        "type": "session.started",
        "stage": "session",
        "payload": {"session_id": session_id, "provider": provider, "model": model},
    })
    for i in range(turns):
        lines.append({
            "ts": f"2026-05-23T12:00:{(i+1)*5:02d}.000+00:00",
            "type": "turn.started",
            "payload": {"user_input": "do the thing"},
        })
        for _ in range(tool_calls):
            lines.append({
                "ts": f"2026-05-23T12:00:{(i+1)*5+1:02d}.000+00:00",
                "type": "tool.call.started",
                "payload": {"name": "ls", "input": {"path": "."}},
            })
        lines.append({
            "ts": f"2026-05-23T12:00:{(i+1)*5+2:02d}.000+00:00",
            "type": "llm.call.completed",
            "payload": {
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": final_text}],
            },
        })
        lines.append({
            "ts": f"2026-05-23T12:00:{(i+1)*5+3:02d}.000+00:00",
            "type": "turn.ended",
            "payload": {},
        })
    if aborted_reason:
        lines.append({
            "ts": "2026-05-23T12:00:30.000+00:00",
            "type": "session.aborted",
            "payload": {"reason": aborted_reason, "running_usd": 0.42},
        })
    lines.append({
        "ts": "2026-05-23T12:00:35.000+00:00",
        "type": "session.ended",
        "payload": {},
    })
    return "\n".join(json.dumps(line) for line in lines) + "\n"


def _make_session(tmp_path: Path, name: str, **kwargs) -> Path:
    sess = tmp_path / name
    sess.mkdir()
    (sess / "events.jsonl").write_text(_events_for(name, **kwargs))
    return sess


# ── summarize_session ─────────────────────────────────────────────────────


def test_summarize_extracts_provider_model_turns(tmp_path: Path):
    d = _make_session(tmp_path, "S1", provider="anthropic", model="claude-haiku-4-5",
                       turns=3, tokens_in=200, tokens_out=80, tool_calls=1)
    s = summarize_session(d)
    assert s.provider == "anthropic"
    assert s.model == "claude-haiku-4-5"
    assert s.turns == 3
    assert s.tool_calls == 3
    assert s.input_tokens == 600
    assert s.output_tokens == 240
    assert s.final_response == "done"
    assert s.final_stop_reason == "end_turn"


def test_summarize_picks_up_session_aborted(tmp_path: Path):
    d = _make_session(tmp_path, "S2", provider="anthropic", model="x",
                       turns=1, aborted_reason="cost_cap")
    s = summarize_session(d)
    assert s.aborted_reason == "cost_cap"
    assert s.cost_usd == 0.42


def test_summarize_handles_missing_events(tmp_path: Path):
    d = tmp_path / "empty"
    d.mkdir()
    (d / "events.jsonl").write_text("")
    s = summarize_session(d)
    assert s.session_id == "empty"
    assert s.turns == 0


def test_summarize_wallclock_uses_first_and_last_ts(tmp_path: Path):
    d = _make_session(tmp_path, "S3", provider="x", model="y", turns=2)
    s = summarize_session(d)
    assert s.wallclock_seconds > 0


# ── render_summary_table ──────────────────────────────────────────────────


def test_summary_table_handles_two_sessions(tmp_path: Path):
    a = summarize_session(_make_session(tmp_path, "A", provider="anthropic", model="x"))
    b = summarize_session(_make_session(tmp_path, "B", provider="ollama", model="y"))
    text = render_summary_table([a, b])
    assert "anthropic/x" in text
    assert "ollama/y" in text
    assert "Tool calls" in text
    assert "Cost (USD)" in text


def test_summary_table_handles_three_sessions(tmp_path: Path):
    sessions = []
    for name, p in [("A", "anthropic"), ("B", "gemini"), ("C", "ollama")]:
        sessions.append(summarize_session(
            _make_session(tmp_path, name, provider=p, model=p + "-model")
        ))
    text = render_summary_table(sessions)
    for p in ("anthropic", "gemini", "ollama"):
        assert p in text


def test_summary_table_shows_aborted_badge(tmp_path: Path):
    s = summarize_session(_make_session(
        tmp_path, "A", provider="anthropic", model="x",
        aborted_reason="cost_cap",
    ))
    other = summarize_session(_make_session(tmp_path, "B", provider="ollama", model="y"))
    text = render_summary_table([s, other])
    assert "cost_cap" in text


# ── extract_turns ─────────────────────────────────────────────────────────


def test_extract_turns_returns_one_per_turn(tmp_path: Path):
    d = _make_session(tmp_path, "S", provider="x", model="y", turns=4, tool_calls=2)
    turns = extract_turns(d)
    assert len(turns) == 4
    assert all(t.user_input == "do the thing" for t in turns)
    assert all(len(t.tool_calls) == 2 for t in turns)
    assert all(t.assistant_text == "done" for t in turns)


# ── render_full_comparison ────────────────────────────────────────────────


def test_full_comparison_two_sessions_includes_turn_by_turn(tmp_path: Path):
    a = _make_session(tmp_path, "A", provider="anthropic", model="x",
                      turns=2, final_text="from-a")
    b = _make_session(tmp_path, "B", provider="ollama", model="y",
                      turns=2, final_text="from-b")
    out = render_full_comparison([a, b])
    assert "anthropic/x" in out
    assert "ollama/y" in out
    assert "Turn-by-turn" in out


def test_full_comparison_three_sessions_summary_only(tmp_path: Path):
    sessions = [
        _make_session(tmp_path, "A", provider="anthropic", model="x"),
        _make_session(tmp_path, "B", provider="gemini", model="y"),
        _make_session(tmp_path, "C", provider="ollama", model="z"),
    ]
    out = render_full_comparison(sessions)
    assert "Turn-by-turn" not in out
    assert "anthropic" in out
    assert "gemini" in out
    assert "ollama" in out
