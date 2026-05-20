"""Phase 2.3 logging acceptance test.

Run a real session against Gemini. Verify session.log:
  - exists in the session dir
  - contains banners
  - contains the user input
  - contains the tool call(s)
  - contains the assistant response
  - lines are v1-format (timestamp + level + logger name)

Also test `arc log <session_id>` CLI subcommand.
"""
from __future__ import annotations

import json
import os
import re
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
    for n in ("a.txt", "b.txt", "c.txt"):
        (workspace / n).write_text(n)

    monkeypatch.setenv("ARC_HOME", str(home))
    main(["bootstrap"])
    cfg = (home / "config.yml").read_text()
    cfg = cfg.replace('workspace: "."', f'workspace: "{workspace}"')
    (home / "config.yml").write_text(cfg)
    return home, workspace


def _latest_session_id(home: Path) -> str:
    sessions = sorted([p for p in (home / "sessions").iterdir() if p.is_dir()])
    return sessions[-1].name


# ── session.log content ────────────────────────────────────────────────────


def test_session_log_is_written_alongside_events(world):
    home, workspace = world
    main(["run", f"List files in {workspace}"])
    sid = _latest_session_id(home)
    session_dir = home / "sessions" / sid

    assert (session_dir / "session.log").is_file()
    assert (session_dir / "events.jsonl").is_file()


def test_session_log_contains_banners_and_user_input(world):
    home, workspace = world
    main(["run", f"List files in {workspace}"])
    sid = _latest_session_id(home)
    text = (home / "sessions" / sid / "session.log").read_text()

    assert "Session started" in text
    assert "── Turn" in text
    assert "user:" in text
    assert f"List files in {workspace}" in text
    assert "Session ended" in text


def test_session_log_contains_tool_calls_and_response(world):
    home, workspace = world
    main(["run", f"List files in {workspace}"])
    sid = _latest_session_id(home)
    text = (home / "sessions" / sid / "session.log").read_text()

    assert "ls(" in text  # tool call line
    assert "← ls" in text  # tool completion
    # The assistant said something about the files
    assert "assistant:" in text


def test_session_log_lines_use_v1_format(world):
    home, workspace = world
    main(["run", f"List files in {workspace}"])
    sid = _latest_session_id(home)
    text = (home / "sessions" / sid / "session.log").read_text()

    line_re = re.compile(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} "
        r"\[(INFO|WARNING|ERROR|DEBUG)\] "
        r"arc\.\w+: "
    )
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # Most lines should match the v1 format; banner separator lines are pure
    # equals/dashes so allow some non-matching but require a strong majority
    matching = [ln for ln in lines if line_re.match(ln)]
    assert len(matching) >= len(lines) * 0.7


def test_log_writer_does_not_pollute_events_jsonl(world):
    """log_writer's records must NOT show up as new events in events.jsonl."""
    home, workspace = world
    main(["run", f"List files in {workspace}"])
    sid = _latest_session_id(home)

    events = [
        json.loads(l)
        for l in (home / "sessions" / sid / "events.jsonl").read_text().splitlines()
        if l.strip()
    ]
    # No event type should mention "log" — log_writer is a consumer, not producer
    types = {e["type"] for e in events}
    for t in types:
        assert "log" not in t.lower()


# ── arc log CLI ────────────────────────────────────────────────────────────


def test_arc_log_cli_prints_session_log(world, capsys):
    home, workspace = world
    main(["run", f"List files in {workspace}"])
    sid = _latest_session_id(home)
    capsys.readouterr()  # drain

    rc = main(["log", sid])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Session started" in captured.out
    assert "user:" in captured.out


def test_arc_log_cli_tail_shows_last_n(world, capsys):
    home, workspace = world
    main(["run", f"List files in {workspace}"])
    sid = _latest_session_id(home)
    capsys.readouterr()

    rc = main(["log", sid, "--tail", "5"])
    captured = capsys.readouterr()
    assert rc == 0
    lines = [ln for ln in captured.out.splitlines() if ln.strip()]
    assert len(lines) <= 5


def test_arc_log_cli_missing_session_errors(world, capsys):
    home, _ = world
    rc = main(["log", "SES_does_not_exist"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no session.log" in captured.err
