"""Process management for the local inference server.

PID file + log file under `$ARC_HOME/llm/`.  The single-server model means
there's at most one PID file (`current.pid`) and one log (`current.log`)
per ARC_HOME.

`start_new_session=True` detaches the child so killing arc itself doesn't
take down the server.  The CLI returns to the user once /health is OK,
the server keeps running, and `arc llm stop` later sends it SIGTERM.
"""
from __future__ import annotations

import errno
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


PID_FILENAME = "current.pid"
LOG_FILENAME = "current.log"


@dataclass(frozen=True)
class PidState:
    """What `current.pid` records: pid, model_id, started_at (ISO)."""
    pid: int
    model_id: str
    started_at: str

    @property
    def started_dt(self) -> datetime:
        return datetime.fromisoformat(self.started_at)


class ProcessError(RuntimeError):
    """Failed to start/stop the inference server."""


# ── PID-file helpers ──────────────────────────────────────────────────────


def read_pid_file(pid_path: Path) -> PidState | None:
    """Return the recorded PidState, or None if no/stale file.

    A stale file (recorded pid not running) is silently removed and
    treated as missing.
    """
    if not pid_path.exists():
        return None
    try:
        data = json.loads(pid_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupted; treat as nothing-running and clean it up
        _safe_unlink(pid_path)
        return None

    pid = int(data.get("pid") or 0)
    if pid <= 0 or not _pid_alive(pid):
        _safe_unlink(pid_path)
        return None
    return PidState(
        pid=pid,
        model_id=str(data.get("model_id") or ""),
        started_at=str(data.get("started_at") or ""),
    )


def _write_pid_file(pid_path: Path, *, pid: int, model_id: str) -> None:
    """Atomically create the pid file (O_CREAT | O_EXCL).

    Two concurrent starts → only one wins; the other gets FileExistsError
    which the caller maps to a clear error.
    """
    payload = json.dumps({
        "pid": pid,
        "model_id": model_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
    })
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(pid_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _pid_alive(pid: int) -> bool:
    """POSIX: kill(pid, 0) raises ESRCH if no such process."""
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM  # exists but we can't signal it
    return True


# ── Start / stop / status ─────────────────────────────────────────────────


@dataclass
class StartResult:
    pid: int
    model_id: str
    started_at: str
    health_elapsed_seconds: float
    health_ok: bool


def start(
    *,
    llm_dir: Path,
    argv: list[str],
    model_id: str,
    base_url: str,
    startup_timeout_seconds: int,
    progress_cb=None,
) -> StartResult:
    """Spawn the server, write PID file, poll /health until ok.

    Caller is responsible for:
      - Having stopped any prior server (caller should check status first
        and only call start() when no different model is loaded).
      - Choosing the base_url that matches the argv's --port/--host.

    Raises ProcessError on race conditions or spawn failure.  Does NOT
    raise on /health timeout; returns `health_ok=False` so the CLI can
    print a clear "server may still be loading" message.
    """
    pid_path = llm_dir / PID_FILENAME
    log_path = llm_dir / LOG_FILENAME
    llm_dir.mkdir(parents=True, exist_ok=True)

    # Open log in append mode so successive starts grow the same file
    log_fd = open(log_path, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            argv,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # detach: server outlives the CLI
            close_fds=True,
        )
    except (FileNotFoundError, OSError) as exc:
        log_fd.close()
        raise ProcessError(
            f"failed to spawn {argv[0]!r}: {exc}\n"
            f"  check binary.path in llm_servers.yml and that the file is executable"
        ) from exc

    # Write PID file atomically; race-loser cleans up.
    try:
        _write_pid_file(pid_path, pid=proc.pid, model_id=model_id)
    except FileExistsError:
        # Another arc llm start beat us to it.  Kill our spawn so we don't
        # leave a zombie.
        try:
            proc.terminate()
        except Exception:
            pass
        log_fd.close()
        raise ProcessError(
            "another `arc llm start` is in progress (or a stale pid file "
            "exists). retry once it finishes, or run `arc llm stop` if "
            "you think the file is stale."
        )

    # Don't close log_fd — Popen needs it for stdout/stderr.

    # Poll /health
    from arc.llm.health import wait_for_healthy
    started = time.monotonic()
    ok = wait_for_healthy(
        base_url=base_url,
        timeout_seconds=startup_timeout_seconds,
        progress_cb=progress_cb,
    )
    elapsed = time.monotonic() - started

    state = read_pid_file(pid_path)
    return StartResult(
        pid=state.pid if state else proc.pid,
        model_id=model_id,
        started_at=state.started_at if state else "",
        health_elapsed_seconds=elapsed,
        health_ok=ok,
    )


def stop(*, llm_dir: Path, term_timeout_seconds: float = 10.0) -> bool:
    """SIGTERM the running server; SIGKILL after timeout.

    Returns True if a server was running and is now stopped, False if
    there was nothing to stop.
    """
    pid_path = llm_dir / PID_FILENAME
    state = read_pid_file(pid_path)
    if state is None:
        return False

    try:
        os.kill(state.pid, signal.SIGTERM)
    except OSError:
        # Process gone between status read and kill — cleanup file and return
        _safe_unlink(pid_path)
        return True

    deadline = time.monotonic() + term_timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_alive(state.pid):
            _safe_unlink(pid_path)
            return True
        time.sleep(0.5)

    # Didn't exit gracefully — force kill
    try:
        os.kill(state.pid, signal.SIGKILL)
    except OSError:
        pass
    _safe_unlink(pid_path)
    return True


def status(*, llm_dir: Path) -> PidState | None:
    """Public read-through to the current pid file."""
    return read_pid_file(llm_dir / PID_FILENAME)


def tail_log(*, llm_dir: Path, n: int = 100) -> str:
    """Return the last `n` lines of current.log (or '' if no log)."""
    log_path = llm_dir / LOG_FILENAME
    if not log_path.is_file():
        return ""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-n:])
