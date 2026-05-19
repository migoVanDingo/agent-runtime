"""Phase 2.1.5 acceptance: pause + resume.

The scenarios:

  test_pause_via_signal_file_stops_turn_cleanly
    Start a session with a workflow → register a "trigger pause on first
    pause_check" plugin → assert agent stopped, turn ended with paused=true.

  test_resume_continues_with_restored_messages
    Take a recorded (paused) session → arc resume <id> --prompt → assert
    new session has resumed_from + restored_message_count, and the
    LLM saw the prior conversation.

  test_resume_from_completed_session_works_too
    Resume on a normally-ended session — should work (no pause required).

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
    """ARC_HOME + empty workspace, ready to run a session."""
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


def _latest_session_id(home: Path) -> str:
    sessions = sorted([p for p in (home / "sessions").iterdir() if p.is_dir()])
    assert sessions, "no session created"
    return sessions[-1].name


def _events(home: Path, sid: str) -> list[dict]:
    p = home / "sessions" / sid / "events.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ── Pause via signal file ─────────────────────────────────────────────────


def test_pause_via_signal_file_stops_turn_cleanly(world, monkeypatch):
    """Trigger pause from a hook that fires on the 2nd pause_check.
    This deterministically pauses mid-turn without timing tricks.
    """
    home, workspace = world

    # Inject a pause-triggering hook by registering after plugin construction.
    # We do this by monkey-patching the pause-resume plugin's pause_check
    # to count calls and trigger on the 2nd one.
    from arc.plugins.pause_resume import PauseResumePlugin
    original_pause_check = PauseResumePlugin.pause_check

    call_counter = {"n": 0}
    def counting_pause_check(self, ctx):
        call_counter["n"] += 1
        if call_counter["n"] == 2:
            # Trigger pause on the 2nd pause_check (well into the loop)
            self.request_pause()
        return original_pause_check(self, ctx)

    monkeypatch.setattr(PauseResumePlugin, "pause_check", counting_pause_check)

    # Multi-step workflow guarantees at least 2 iterations
    prompt = (
        f"Create directory {workspace}/p, write a one-line haiku to "
        f"{workspace}/p/h.txt, then read it back and summarize."
    )
    rc = main(["run", prompt])

    # Pause manifests as a non-success turn
    assert rc == 1, f"expected rc=1 from paused turn, got {rc}"

    sid = _latest_session_id(home)
    events = _events(home, sid)
    turn_ended = next(e for e in events if e["type"] == "turn.ended")
    assert turn_ended["payload"]["success"] is False
    assert turn_ended["payload"]["error"] == "paused"

    # session.ended still fires — recorder closes the session cleanly
    assert any(e["type"] == "session.ended" for e in events)


# ── Resume continues with restored messages ─────────────────────────────


def test_resume_continues_with_restored_messages(world, capsys):
    """Record a session, then resume it — assert the LLM saw prior history."""
    home, workspace = world

    # Run an initial session
    main(["run", f"List files in {workspace}"])
    capsys.readouterr()
    original_sid = _latest_session_id(home)

    # Resume it with a follow-up prompt
    rc = main(["resume", original_sid, "--prompt",
               "How many files were in that directory?"])
    captured = capsys.readouterr()
    assert rc == 0

    # A new session was created
    sessions = sorted([p.name for p in (home / "sessions").iterdir() if p.is_dir()])
    assert len(sessions) == 2
    resumed_sid = [s for s in sessions if s != original_sid][0]

    # Meta points back at original + records restored count
    meta = json.loads((home / "sessions" / resumed_sid / "meta.json").read_text())
    assert meta["resumed_from"] == original_sid
    assert meta["restored_message_count"] >= 1

    # The resumed session's FIRST llm.call.started must contain the prior
    # conversation, not just the new user message
    events = _events(home, resumed_sid)
    first_llm = next(e for e in events if e["type"] == "llm.call.started")
    msgs = first_llm["content"]["messages"]
    # At minimum: prior user + prior assistant + (maybe tool messages) + new user
    assert len(msgs) >= 3
    # Last message is the new user prompt
    assert msgs[-1]["role"] == "user"
    assert "How many files" in msgs[-1]["content"]


def test_resume_summary_message_mentions_prior_context(world, capsys):
    """Sanity check that the model picks up the prior conversation."""
    home, workspace = world
    # Create three known files so the assistant has something to count
    for n in ("alpha.txt", "beta.txt", "gamma.txt"):
        (workspace / n).write_text(n)

    main(["run", f"List the files in {workspace}"])
    capsys.readouterr()
    original_sid = _latest_session_id(home)

    rc = main(["resume", original_sid,
               "--prompt", "How many files did you just list? Answer with one number."])
    captured = capsys.readouterr()
    assert rc == 0
    # Stdout has the assistant's final answer — should contain "3"
    assert "3" in captured.out


# ── Resume from a non-paused session works too ──────────────────────────


def test_resume_from_completed_session(world, capsys):
    home, workspace = world

    main(["run", f"List files in {workspace}"])
    capsys.readouterr()
    sid = _latest_session_id(home)

    # Session completed normally (no pause)
    meta = json.loads((home / "sessions" / sid / "meta.json").read_text())
    assert meta.get("last_outcome", {}).get("success") is True

    # Resume should still work
    rc = main(["resume", sid, "--prompt", "Thanks!"])
    assert rc == 0


# ── Resume on a missing session is a clean error ────────────────────────


def test_resume_missing_session_returns_error(world, capsys):
    home, _ = world
    rc = main(["resume", "SES_does_not_exist", "--prompt", "x"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "session not found" in captured.err
