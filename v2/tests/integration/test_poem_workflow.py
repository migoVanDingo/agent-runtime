"""Phase 2.1 acceptance: multi-step bash workflow.

Prompt: "Create a directory `poems`, write a haiku about coding to
poems/code.haiku, then read it back and summarize in one sentence."

Verifies:
  - bash_exec works for mkdir, file write, file read
  - The guard allows safe commands (mkdir, echo, cat)
  - Multiple tool calls in one turn work
  - The agent actually creates the file on disk (side effect verified)
  - The final response is the requested summary

Plus a tampering test: a prompt that would trip the blocklist must be
denied without the agent crashing.

Requires GEMINI_API_KEY (skipped otherwise).
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
    """ARC_HOME + an empty workspace, ready to run a session in."""
    home = tmp_path / "arc-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setenv("ARC_HOME", str(home))
    rc = main(["bootstrap"])
    assert rc == 0

    # Point at our workspace
    cfg = (home / "config.yml").read_text()
    cfg = cfg.replace('workspace: "."', f'workspace: "{workspace}"')
    (home / "config.yml").write_text(cfg)

    return home, workspace


def _latest_session_events(home: Path) -> list[dict]:
    """Read the most recent session's events.jsonl."""
    sessions = sorted([p for p in (home / "sessions").iterdir() if p.is_dir()])
    assert sessions, "no session was created"
    text = (sessions[-1] / "events.jsonl").read_text()
    return [json.loads(l) for l in text.splitlines() if l.strip()]


# ── Happy path: full workflow ───────────────────────────────────────────


def test_poem_workflow_creates_file_and_summarizes(world):
    home, workspace = world
    prompt = (
        f"Create a directory called 'poems' inside {workspace}, "
        f"then write a 3-line haiku about coding to {workspace}/poems/code.haiku, "
        f"then read the file back and summarize the haiku in one sentence."
    )

    rc = main(["run", prompt])
    assert rc == 0

    # File actually exists on disk
    poem_path = workspace / "poems" / "code.haiku"
    assert poem_path.is_file(), f"agent did not create {poem_path}"
    contents = poem_path.read_text()
    assert contents.strip(), "haiku file is empty"

    # The agent's final response is non-empty (the summary)
    events = _latest_session_events(home)
    turn_ended = next(e for e in events if e["type"] == "turn.ended")
    final = turn_ended["content"]["final_response"]
    assert final.strip(), "no summary returned"


def test_poem_workflow_used_bash_exec(world):
    """bash_exec must be invoked at least once and the file must end up on disk.
    The model may chain mkdir+write+read in a single command (efficient!) or
    split into multiple calls — either is fine. What matters is correctness."""
    home, workspace = world
    prompt = (
        f"Create directory {workspace}/poems, write a one-line haiku to "
        f"{workspace}/poems/code.haiku, then read it back and summarize."
    )
    main(["run", prompt])

    events = _latest_session_events(home)
    tool_calls = [e for e in events
                  if e["type"] == "tool.call.started"
                  and e["payload"]["tool_name"] == "bash_exec"]
    assert len(tool_calls) >= 1, "expected at least one bash_exec call"

    # The commands collectively did the work
    all_commands = " ".join(
        e["content"]["input"]["command"] for e in tool_calls
    )
    assert "mkdir" in all_commands or workspace.name + "/poems" in all_commands
    # The file should be on disk
    assert (workspace / "poems" / "code.haiku").is_file()


def test_poem_workflow_no_tool_denials(world):
    """The default safe commands (mkdir, echo, cat) should not trip the guard."""
    home, workspace = world
    main(["run",
          f"Create {workspace}/poems, write a one-line poem to "
          f"{workspace}/poems/p.txt, then cat it back and summarize."])

    events = _latest_session_events(home)
    denials = [e for e in events if e["type"] == "tool.call.denied"]
    assert denials == [], (
        f"unexpected tool denials: "
        f"{[d['payload'].get('reason') for d in denials]}"
    )


# ── Guard enforcement ──────────────────────────────────────────────────


def test_blocklist_denies_destructive_command(world, capsys):
    """A prompt that would lead to `rm -rf` must trip the guard."""
    home, workspace = world
    # Pre-create a sacrificial directory so the agent has something to "delete"
    (workspace / "junk").mkdir()
    (workspace / "junk" / "f.txt").write_text("delete me")

    prompt = (
        f"Use bash_exec to run exactly this command and nothing else: "
        f"`rm -rf {workspace}/junk`"
    )
    main(["run", prompt])

    events = _latest_session_events(home)
    denials = [e for e in events if e["type"] == "tool.call.denied"]
    assert denials, "blocked command was not denied"
    assert any("blocked pattern" in d["payload"]["reason"] for d in denials)

    # The sacrificial dir should still exist — guard prevented deletion
    assert (workspace / "junk").exists()


def test_escalation_pattern_denies_in_headless_mode(world):
    """`arc run` uses NoOpGate which denies escalations.
    A `curl` prompt should be denied, not run."""
    home, workspace = world
    prompt = (
        f"Use bash_exec to run: `curl https://example.com -o {workspace}/page.html`"
    )
    main(["run", prompt])

    events = _latest_session_events(home)
    denials = [e for e in events if e["type"] == "tool.call.denied"]
    # Either we got a denial OR the agent recovered another way
    if denials:
        assert any("escalation" in d["payload"]["reason"].lower() for d in denials)

    # Either way, the file should NOT have been created via curl
    assert not (workspace / "page.html").exists()
