"""Unit tests for `arc.llm.process` — PID file + lifecycle.

The actual subprocess.Popen + os.kill are mocked; the test exercises the
state machine (write PID, race detection, stale cleanup, health-poll
gating).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from arc.llm.process import (
    PID_FILENAME,
    ProcessError,
    read_pid_file,
    start as _start,
    status as _status,
    stop as _stop,
)


# ── PID file helpers ──────────────────────────────────────────────────────


def test_read_pid_file_missing_returns_none(tmp_path: Path):
    assert read_pid_file(tmp_path / PID_FILENAME) is None


def test_read_pid_file_corrupt_returns_none_and_cleans(tmp_path: Path):
    pid_path = tmp_path / PID_FILENAME
    pid_path.write_text("not json")
    assert read_pid_file(pid_path) is None
    assert not pid_path.exists()  # cleaned up


def test_read_pid_file_stale_returns_none_and_cleans(tmp_path: Path):
    """If the recorded pid doesn't exist on the system, file is treated as stale."""
    pid_path = tmp_path / PID_FILENAME
    pid_path.write_text(json.dumps({
        "pid": 999_999,  # very unlikely to be an active pid
        "model_id": "x",
        "started_at": "2026-01-01T00:00:00+00:00",
    }))
    assert read_pid_file(pid_path) is None
    assert not pid_path.exists()


def test_read_pid_file_live_returns_state(tmp_path: Path):
    """Use our own pid — guaranteed to be alive during the test."""
    pid_path = tmp_path / PID_FILENAME
    pid_path.write_text(json.dumps({
        "pid": os.getpid(),
        "model_id": "llama-3.1-8b",
        "started_at": "2026-05-23T12:00:00+00:00",
    }))
    state = read_pid_file(pid_path)
    assert state is not None
    assert state.pid == os.getpid()
    assert state.model_id == "llama-3.1-8b"


# ── start() ────────────────────────────────────────────────────────────────


def _mock_popen(pid: int = 42):
    """Make subprocess.Popen return a MagicMock with .pid = pid."""
    def factory(*args, **kwargs):
        proc = MagicMock()
        proc.pid = pid
        return proc
    return factory


def test_start_spawns_writes_pid_file_and_polls_health(tmp_path: Path):
    llm_dir = tmp_path / "llm"
    with patch("subprocess.Popen", side_effect=_mock_popen(123)) as mock_popen, \
         patch("arc.llm.health.wait_for_healthy", return_value=True) as mock_health:
        result = _start(
            llm_dir=llm_dir,
            argv=["fake-server", "-m", "x.gguf"],
            model_id="x",
            base_url="http://127.0.0.1:8080/v1",
            startup_timeout_seconds=5,
        )
    assert result.pid == 123
    assert result.health_ok is True
    pid_path = llm_dir / PID_FILENAME
    assert pid_path.exists()
    data = json.loads(pid_path.read_text())
    assert data["pid"] == 123
    assert data["model_id"] == "x"

    # Popen got start_new_session=True (detaches the child)
    kwargs = mock_popen.call_args.kwargs
    assert kwargs.get("start_new_session") is True
    mock_health.assert_called_once()


def test_start_health_timeout_returns_health_ok_false(tmp_path: Path):
    llm_dir = tmp_path / "llm"
    with patch("subprocess.Popen", side_effect=_mock_popen(123)), \
         patch("arc.llm.health.wait_for_healthy", return_value=False):
        result = _start(
            llm_dir=llm_dir,
            argv=["fake"],
            model_id="x",
            base_url="http://127.0.0.1:8080/v1",
            startup_timeout_seconds=1,
        )
    assert result.health_ok is False
    # PID file still written — server is "running, just slow to come up"
    assert (llm_dir / PID_FILENAME).exists()


def test_start_race_loser_gets_clear_error(tmp_path: Path):
    """Two concurrent starts → only one wins the O_EXCL pid-file create."""
    llm_dir = tmp_path / "llm"
    llm_dir.mkdir()
    # Pre-create the pid file so our start() loses the race
    (llm_dir / PID_FILENAME).write_text(json.dumps({
        "pid": os.getpid(),
        "model_id": "occupant",
        "started_at": "2026-01-01T00:00:00+00:00",
    }))
    with patch("subprocess.Popen", side_effect=_mock_popen(456)):
        with pytest.raises(ProcessError, match="another `arc llm start`"):
            _start(
                llm_dir=llm_dir,
                argv=["fake"],
                model_id="x",
                base_url="http://127.0.0.1:8080/v1",
                startup_timeout_seconds=1,
            )


def test_start_popen_failure_raises_clearly(tmp_path: Path):
    llm_dir = tmp_path / "llm"
    with patch("subprocess.Popen", side_effect=FileNotFoundError("no such binary")):
        with pytest.raises(ProcessError, match="failed to spawn"):
            _start(
                llm_dir=llm_dir,
                argv=["nonexistent"],
                model_id="x",
                base_url="http://127.0.0.1:8080/v1",
                startup_timeout_seconds=1,
            )


# ── stop() ────────────────────────────────────────────────────────────────


def test_stop_with_no_pid_file_returns_false(tmp_path: Path):
    llm_dir = tmp_path / "llm"
    llm_dir.mkdir()
    assert _stop(llm_dir=llm_dir) is False


def test_stop_sends_sigterm_and_removes_pid(tmp_path: Path):
    """Use our own pid + mocked os.kill so we don't actually signal ourselves.

    _pid_alive must return True on first call (so read_pid_file considers
    the file fresh) and False after (so the poll loop exits)."""
    llm_dir = tmp_path / "llm"
    llm_dir.mkdir()
    (llm_dir / PID_FILENAME).write_text(json.dumps({
        "pid": os.getpid(),
        "model_id": "x",
        "started_at": "2026-01-01T00:00:00+00:00",
    }))
    with patch("os.kill") as mock_kill, \
         patch("arc.llm.process._pid_alive", side_effect=[True, False]):
        result = _stop(llm_dir=llm_dir, term_timeout_seconds=0.5)
    assert result is True
    assert mock_kill.called
    assert not (llm_dir / PID_FILENAME).exists()


def test_stop_force_kills_after_term_timeout(tmp_path: Path):
    llm_dir = tmp_path / "llm"
    llm_dir.mkdir()
    (llm_dir / PID_FILENAME).write_text(json.dumps({
        "pid": os.getpid(),
        "model_id": "x",
        "started_at": "2026-01-01T00:00:00+00:00",
    }))
    # First _pid_alive call: read_pid_file → True (file is fresh).
    # Subsequent calls in the loop: True (still alive), forcing SIGKILL.
    with patch("os.kill") as mock_kill, \
         patch("arc.llm.process._pid_alive", return_value=True):
        _stop(llm_dir=llm_dir, term_timeout_seconds=0.05)
    assert mock_kill.call_count >= 2
    import signal
    sigs_sent = [c.args[1] for c in mock_kill.call_args_list]
    assert signal.SIGTERM in sigs_sent
    assert signal.SIGKILL in sigs_sent


# ── status() ───────────────────────────────────────────────────────────────


def test_status_passthrough_to_read_pid_file(tmp_path: Path):
    llm_dir = tmp_path / "llm"
    llm_dir.mkdir()
    (llm_dir / PID_FILENAME).write_text(json.dumps({
        "pid": os.getpid(),
        "model_id": "z",
        "started_at": "2026-01-01T00:00:00+00:00",
    }))
    state = _status(llm_dir=llm_dir)
    assert state is not None
    assert state.model_id == "z"
