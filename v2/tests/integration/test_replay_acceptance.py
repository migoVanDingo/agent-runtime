"""Phase 2.0.5 acceptance: record → replay → assert byte-identical.

This is the gate spec §10.3 criterion 4 calls out: a recorded session must
be replayable so that the new event log matches the original after
normalization. Catches recorder bugs that structural unit tests miss.

Plus a deliberately-tampered case: mutate the recording and assert the diff
catches it. Proves divergence detection isn't silently no-op.

Requires GEMINI_API_KEY (skipped otherwise) — the recording phase hits
the real API.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from arc.cli import main
from arc.replay import diff_event_logs


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
def recorded_session(tmp_path, monkeypatch):
    """Record a hello-world session against real Gemini, return (home, sid)."""
    home = tmp_path / "arc-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (workspace / name).write_text(name)

    monkeypatch.setenv("ARC_HOME", str(home))

    # Bootstrap + point at our workspace
    rc = main(["bootstrap"])
    assert rc == 0
    cfg = (home / "config.yml").read_text()
    cfg = cfg.replace('workspace: "."', f'workspace: "{workspace}"')
    (home / "config.yml").write_text(cfg)

    # Record
    rc = main(["run", f"List the files in {workspace}"])
    assert rc == 0

    sessions = sorted([p for p in (home / "sessions").iterdir() if p.is_dir()])
    assert len(sessions) == 1
    return home, sessions[0].name


# ── Mode 2: deterministic replay ───────────────────────────────────────────


def test_mode_2_replay_matches_original(recorded_session, capsys):
    """The criterion-4 gate. Replay the recording and assert no divergence."""
    home, sid = recorded_session

    rc = main(["replay", sid])
    captured = capsys.readouterr()
    assert rc == 0, (
        f"replay returned {rc}\nstdout: {captured.out}\nstderr: {captured.err}"
    )
    assert "replay matches" in captured.out


def test_replay_creates_new_session_with_replay_of_marker(recorded_session):
    """The replayed session is its own dir, with meta.json pointing back."""
    home, sid = recorded_session
    main(["replay", sid])

    session_dirs = sorted([p for p in (home / "sessions").iterdir() if p.is_dir()])
    assert len(session_dirs) == 2  # original + replay

    replay_dir = next(p for p in session_dirs if p.name != sid)
    meta = json.loads((replay_dir / "meta.json").read_text())
    assert meta["replay_of"] == sid
    assert meta["replay_mode"] == "in_order"


def test_replayed_events_jsonl_normalizes_to_original(recorded_session):
    """Independent of the CLI's matched check, the diff infrastructure itself
    should say the two logs match."""
    home, sid = recorded_session
    main(["replay", sid])

    session_dirs = sorted([p for p in (home / "sessions").iterdir() if p.is_dir()])
    original = home / "sessions" / sid / "events.jsonl"
    replayed = next(p for p in session_dirs if p.name != sid) / "events.jsonl"

    result = diff_event_logs(original, replayed)
    assert result.matched, f"\n{result.unified_diff}"


# ── Divergence detection ─────────────────────────────────────────────────


def test_tampered_recording_is_caught(recorded_session, capsys, monkeypatch):
    """Mutate the recording (change a tool output) → replay must diverge.

    This proves the gate isn't silently passing — if we mutate the source-of-
    truth, the replay's own (correct) behavior won't match, and the diff
    layer must catch it.

    NOTE: this test re-uses the recorded session and rewrites events.jsonl.
    Since the fixture is function-scoped, that's fine.
    """
    home, sid = recorded_session

    events_path = home / "sessions" / sid / "events.jsonl"
    lines = events_path.read_text().splitlines()

    # Find a tool.call.completed line and mutate its output
    tampered = []
    mutated = False
    for line in lines:
        e = json.loads(line)
        if not mutated and e.get("type") == "tool.call.completed":
            e["content"]["output"] = "TAMPERED OUTPUT — not what really happened"
            mutated = True
        tampered.append(json.dumps(e, ensure_ascii=False, separators=(",", ":")))
    assert mutated, "no tool.call.completed found to tamper with"

    events_path.write_text("\n".join(tampered) + "\n")

    rc = main(["replay", sid])
    captured = capsys.readouterr()
    assert rc == 1, "tampered replay should have failed"
    assert "DIVERGED" in captured.err
    assert "first divergence" in captured.err
