"""Phase 2.2 acceptance: branch (mode 4) + rerun (mode 5).

Branch: record a 2-turn session, branch at turn 1 with a different prompt,
        verify the new session restored only turn 1.

Rerun: record a session, rerun it with a fresh agent, verify a new
       session was created and the original user inputs were re-issued.

Requires GEMINI_API_KEY.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from arc.cli import main


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "arc-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setenv("ARC_HOME", str(home))
    rc = main(["bootstrap"])
    assert rc == 0
    cfg = (home / "config.yml").read_text()
    cfg = cfg.replace('workspace: "."', f'workspace: "{workspace}"')
    (home / "config.yml").write_text(cfg)
    return home, workspace


def _session_dirs(home: Path) -> list[Path]:
    return sorted([p for p in (home / "sessions").iterdir() if p.is_dir()])


def _events(session_dir: Path) -> list[dict]:
    return [json.loads(l) for l in
            (session_dir / "events.jsonl").read_text().splitlines() if l.strip()]


# ── Branch (mode 4) ────────────────────────────────────────────────────


def test_branch_at_turn_1_restores_only_first_turn(world, capsys):
    home, workspace = world
    # Pre-create some files so the LLM has predictable output
    for n in ("a.txt", "b.txt"):
        (workspace / n).write_text(n)

    # Build a 2-turn recording: first list files, then ask about them
    main(["run", f"List the files in {workspace}"])
    sid1 = _session_dirs(home)[0].name
    main(["resume", sid1, "--prompt", "How many files were there?"])
    sids_after = _session_dirs(home)
    # The resumed session is the new one
    original = next(p for p in sids_after if p.name == sid1)

    # Capture how many turns the original session has via the resumed_from chain
    # Actually the recording IS sid1's events.jsonl — that has 1 completed turn
    # because resume creates a NEW session, not extends sid1.
    original_turns = sum(
        1 for e in _events(original) if e["type"] == "turn.ended"
    )
    assert original_turns == 1

    capsys.readouterr()

    # Branch at turn 1 with a different prompt
    rc = main(["resume", sid1, "--at-turn", "1",
               "--prompt", "What's the weather like?"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "branch @ turn 1" in captured.out

    # Find the new branched session
    branched_dir = max(_session_dirs(home), key=lambda p: p.stat().st_mtime)
    meta = json.loads((branched_dir / "meta.json").read_text())
    assert meta["resumed_from"] == sid1
    assert meta["branched_at_turn"] == 1

    # The branched session's first llm.call.started should have:
    #   prior turn 1 (user + assistant maybe with tool calls + tool result + maybe synthesis)
    #   + the new user message
    # NOT a turn 2 from the original recording
    branched_events = _events(branched_dir)
    first_llm = next(e for e in branched_events if e["type"] == "llm.call.started")
    msgs = first_llm["content"]["messages"]
    # Last message must be the new branch prompt, not a continuation
    assert msgs[-1]["role"] == "user"
    assert "weather" in msgs[-1]["content"]


def test_branch_at_turn_0_starts_fresh(world, capsys):
    home, workspace = world
    main(["run", f"List files in {workspace}"])
    sid = _session_dirs(home)[0].name
    capsys.readouterr()

    rc = main(["resume", sid, "--at-turn", "0", "--prompt", "Hello, fresh start"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "fresh session" in captured.err  # the warning printed

    branched_dir = max(_session_dirs(home), key=lambda p: p.stat().st_mtime)
    meta = json.loads((branched_dir / "meta.json").read_text())
    assert meta["restored_message_count"] == 0

    # First llm.call.started has only the new user message
    first_llm = next(e for e in _events(branched_dir)
                     if e["type"] == "llm.call.started")
    assert len(first_llm["content"]["messages"]) == 1
    assert first_llm["content"]["messages"][0]["content"] == "Hello, fresh start"


def test_branch_at_turn_higher_than_available_clamps(world, capsys):
    home, workspace = world
    main(["run", f"List files in {workspace}"])
    sid = _session_dirs(home)[0].name
    capsys.readouterr()

    rc = main(["resume", sid, "--at-turn", "99",
               "--prompt", "anything"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "clamping" in captured.err


# ── Rerun (mode 5) ─────────────────────────────────────────────────────


def test_rerun_replays_user_inputs(world, capsys):
    home, workspace = world
    for n in ("x.txt", "y.txt"):
        (workspace / n).write_text(n)

    # Record an original session
    main(["run", f"List the files in {workspace}"])
    sid = _session_dirs(home)[0].name
    capsys.readouterr()

    rc = main(["rerun", sid])
    captured = capsys.readouterr()
    assert rc == 0

    # New session exists and is marked rerun_of
    sessions = _session_dirs(home)
    assert len(sessions) == 2
    rerun_dir = next(p for p in sessions if p.name != sid)
    meta = json.loads((rerun_dir / "meta.json").read_text())
    assert meta["rerun_of"] == sid
    assert meta["rerun_turns_attempted"] == 1
    assert meta["rerun_turns_succeeded"] == 1

    # The rerun session's user input matches the original
    rerun_events = _events(rerun_dir)
    rerun_turn = next(e for e in rerun_events if e["type"] == "turn.started")
    orig_events = _events(home / "sessions" / sid)
    orig_turn = next(e for e in orig_events if e["type"] == "turn.started")
    assert rerun_turn["content"]["user_input"] == orig_turn["content"]["user_input"]


def test_rerun_handles_multi_turn_recording(world, capsys):
    home, workspace = world
    (workspace / "f.txt").write_text("hello")

    # Build a 2-turn original
    main(["run", f"List the files in {workspace}"])
    sid1 = _session_dirs(home)[0].name
    main(["resume", sid1, "--prompt", "How many were there?"])
    capsys.readouterr()

    # The resumed session is now the latest; rerun it.
    # We don't assert rc==0 — the rerun's prompt ("How many were there?") was
    # designed for a session that had prior context. Without that context,
    # the LLM may loop (cycle detection then aborts). What matters for
    # MECHANICS is that rerun processed the input, reported back, and the
    # new session was marked rerun_of — not whether the LLM answered well.
    latest = max(_session_dirs(home), key=lambda p: p.stat().st_mtime).name
    rc = main(["rerun", latest])
    captured = capsys.readouterr()

    # Rerun must have RUN — printed its summary line one way or another.
    assert "rerun complete:" in captured.out

    # And the new session must exist and be marked rerun_of
    new_sid = max(_session_dirs(home), key=lambda p: p.stat().st_mtime).name
    meta = json.loads((home / "sessions" / new_sid / "meta.json").read_text())
    assert meta["rerun_of"] == latest


def test_rerun_missing_session_returns_error(world, capsys):
    home, _ = world
    rc = main(["rerun", "SES_does_not_exist"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "session not found" in captured.err


def test_rerun_empty_session_returns_error(world, tmp_path):
    home, _ = world
    # Make an empty session manually
    empty_sid = "SES_empty"
    empty_dir = home / "sessions" / empty_sid
    empty_dir.mkdir(parents=True)
    (empty_dir / "events.jsonl").write_text("")
    (empty_dir / "meta.json").write_text("{}")
    (empty_dir / "config.snapshot.yml").write_text("")

    rc = main(["rerun", empty_sid])
    assert rc == 1
