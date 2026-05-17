"""Ghidra integration via PyGhidra — subprocess dispatch model.

The Ghidra JVM is **never** loaded in the agent process. Every ghidra_*
tool call shells out to ``ghidra_subprocess.py``, which spins up its own
Python interpreter (= its own OS main thread) and runs pyghidra there.

Why subprocess? See ``ghidra_subprocess.py`` docstring. tl;dr: on macOS the
JVM hangs forever when called from any non-main Python thread, and the
agent runs tools on a worker thread. The in-process JVM model verifiably
broke every TUI session that touched Ghidra (SES01KRHKB0BA20926DA6S2WB7QAG
through SES01KRRS8564MYGQGCF3G95XH4M1).

Runtime-as-god alignment: the agent owns the subprocess lifecycle —
spawns it, watches its progress (PROGRESS: lines on stderr → bus events),
kills it on timeout or cancellation. Cancellation is now real: a SIGKILL
on the subprocess actually stops Ghidra, unlike the in-process model
where Java code couldn't be interrupted.

Project caching: ``$ARC_HOME/ghidra/projects/<binary>_ghidra/`` is reused
across calls, so only the first ghidra_* call per binary pays the full
auto-analysis cost.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app_config import settings
from logger import get_logger
from session_paths import arc_home, ghidra_projects_dir

logger = get_logger(__name__)


# Wall-clock cap on individual Ghidra subprocess calls. Tunable via env var.
_DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("ARC_GHIDRA_TIMEOUT", "600"))


def ghidra_home() -> str | None:
    return settings.ghidra_home


def _jvm_log_path() -> Path:
    """Per-process JVM noise log. Subprocess stderr is appended here."""
    p = arc_home() / "logs" / "jvm.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _project_for(binary_path: str) -> tuple[Path, str]:
    """Stable per-binary project location so analysis is cached on disk."""
    name = Path(binary_path).stem + "_ghidra"
    location = ghidra_projects_dir()
    location.mkdir(parents=True, exist_ok=True)
    return location, name


def _project_already_exists(location: Path, name: str) -> bool:
    """A Ghidra project lays down ``<name>.gpr`` somewhere under ``location/name/``."""
    return (location / name / f"{name}.gpr").exists() or (location / f"{name}.gpr").exists()


# ── Subprocess plumbing ──────────────────────────────────────────────────────


def _emit_progress_event(label: str, line: str) -> None:
    """Forward a subprocess progress line to the runtime event bus."""
    try:
        from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
        get_event_bus().emit(RuntimeEvent(
            "runtime.ghidra.progress",
            get_runtime_identity(),
            payload={"label": label, "message": line},
            stage="GhidraSubprocess",
        ))
    except Exception:
        pass


def _emit_lifecycle_event(event_type: str, label: str, **extra: Any) -> None:
    """Emit subprocess.{spawned,killed,exited} bus events."""
    try:
        from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
        payload: dict[str, Any] = {"label": label}
        payload.update(extra)
        severity = "warn" if "killed" in event_type else "info"
        get_event_bus().emit(RuntimeEvent(
            event_type,
            get_runtime_identity(),
            payload=payload,
            stage="GhidraSubprocess",
            severity=severity,
        ))
    except Exception:
        pass


def _drain_stderr(proc: subprocess.Popen, log_fh, label: str) -> None:
    """Read subprocess stderr line by line, log to jvm.log, emit progress events.

    Runs in a daemon thread for the lifetime of the subprocess. Each
    ``PROGRESS: <message>`` line becomes a ``runtime.ghidra.progress`` event;
    everything else is just appended to ``jvm.log`` so JVM diagnostics are
    preserved for debugging.
    """
    try:
        for raw in proc.stderr:  # type: ignore[union-attr]
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            log_fh.write(line + "\n")
            log_fh.flush()
            if line.startswith("PROGRESS: "):
                _emit_progress_event(label, line[len("PROGRESS: "):])
    except Exception:
        # Stream closed or process gone — nothing to do.
        pass


def _wait_with_cancel(
    proc: subprocess.Popen,
    timeout_seconds: float,
    pause_check: Callable[[], None] | None,
) -> tuple[int | None, bool]:
    """Wait for the subprocess to exit, polling pause_check between intervals.

    Returns ``(exit_code, timed_out)``. If ``pause_check`` raises (cancellation),
    we SIGTERM/SIGKILL the subprocess and re-raise — the runtime owns this
    decision, we just enforce it.
    """
    deadline = time.monotonic() + timeout_seconds
    poll_interval = 0.25
    while True:
        rc = proc.poll()
        if rc is not None:
            return rc, False
        if time.monotonic() >= deadline:
            return None, True
        if pause_check is not None:
            try:
                pause_check()
            except BaseException:
                # Cancellation requested — kill the subprocess and propagate.
                _terminate(proc)
                raise
        time.sleep(poll_interval)


def _terminate(proc: subprocess.Popen) -> None:
    """SIGTERM, give it 2s to clean up, then SIGKILL."""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
            return
        except subprocess.TimeoutExpired:
            pass
        proc.kill()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
    except Exception:
        pass


# ── Public API ───────────────────────────────────────────────────────────────


def run_ghidra_op(
    binary_path: str,
    op_name: str,
    params: dict[str, Any] | None = None,
    *,
    timeout_seconds: float | None = None,
    pause_check: Callable[[], None] | None = None,
) -> str:
    """Run one Ghidra operation in a subprocess and return its string result.

    The agent's tool wrappers call this with their op name. Caching, telemetry,
    cancellation, and lifecycle management all happen here so individual tools
    stay one-liners.
    """
    home = ghidra_home()
    if not home:
        return (
            "Error: GHIDRA_HOME not set. Add GHIDRA_HOME=/path/to/ghidra to .env. "
            "Download Ghidra from https://github.com/NationalSecurityAgency/ghidra/releases"
        )

    abs_path = str(Path(binary_path).resolve())
    if not Path(abs_path).exists():
        return f"Error: binary not found at {binary_path!r} (resolved to {abs_path})"

    location, project_name = _project_for(abs_path)
    cached = _project_already_exists(location, project_name)
    if cached:
        logger.info(f"  ghidra: reusing cached project {project_name} (analysis pre-computed)")
    else:
        logger.info(f"  ghidra: first run on {Path(abs_path).name} — full analysis (may take minutes)")

    label = f"{Path(abs_path).name}:{op_name}"
    timeout = timeout_seconds if timeout_seconds is not None else _DEFAULT_TIMEOUT_SECONDS

    cmd = [
        sys.executable,
        "-m",
        "tools.implementations.reversing.ghidra_subprocess",
        "--binary", abs_path,
        "--op", op_name,
        "--params", json.dumps(params or {}, ensure_ascii=False),
        "--project-location", str(location),
        "--project-name", project_name,
        "--ghidra-home", home,
    ]

    # The subprocess needs ``src/`` on PYTHONPATH so it can import ghidra_ops.
    src_dir = str(Path(__file__).resolve().parents[3])
    env = os.environ.copy()
    env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")

    log_path = _jvm_log_path()
    log_fh = open(log_path, "a", encoding="utf-8")
    log_fh.write(f"\n--- {label} @ {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_fh.flush()

    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.getcwd(),
            env=env,
        )
    except Exception as exc:
        log_fh.close()
        return f"Error: failed to spawn Ghidra subprocess: {exc}"

    _emit_lifecycle_event(
        "runtime.ghidra.subprocess.spawned",
        label,
        pid=proc.pid,
        project_cached=cached,
        timeout_seconds=int(timeout),
    )

    drain = threading.Thread(
        target=_drain_stderr, args=(proc, log_fh, label),
        name=f"ghidra-stderr-{proc.pid}", daemon=True,
    )
    drain.start()

    try:
        rc, timed_out = _wait_with_cancel(proc, timeout, pause_check)
    except BaseException as exc:
        # Cancellation propagated up — subprocess already terminated.
        log_fh.close()
        _emit_lifecycle_event(
            "runtime.ghidra.subprocess.killed",
            label, pid=proc.pid, reason="cancelled", elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
        raise

    if timed_out:
        _terminate(proc)
        log_fh.close()
        _emit_lifecycle_event(
            "runtime.ghidra.subprocess.killed",
            label, pid=proc.pid, reason="timeout",
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
        return (
            f"Error: Ghidra subprocess for '{op_name}' on {Path(binary_path).name} "
            f"timed out after {int(timeout)}s and was killed. "
            f"Increase via ARC_GHIDRA_TIMEOUT=<seconds> in .env, or fall back to "
            f"non-Ghidra tools (objdump, nm, strings) for this binary."
        )

    stdout_bytes = b""
    try:
        stdout_bytes = proc.stdout.read() if proc.stdout else b""  # type: ignore[union-attr]
    except Exception:
        pass

    drain.join(timeout=1.0)
    log_fh.close()
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    _emit_lifecycle_event(
        "runtime.ghidra.subprocess.exited",
        label, pid=proc.pid, exit_code=rc, elapsed_ms=elapsed_ms,
    )

    if rc not in (0, 1):
        # 0 = success outcome JSON; 1 = error outcome JSON; anything else = crash.
        return (
            f"Error: Ghidra subprocess crashed with exit code {rc}. "
            f"See {log_path} for JVM diagnostics."
        )

    text = stdout_bytes.decode("utf-8", errors="replace").strip()
    if not text:
        return f"Error: Ghidra subprocess produced no output (see {log_path})"

    try:
        outcome = json.loads(text)
    except json.JSONDecodeError as exc:
        return (
            f"Error: could not parse Ghidra subprocess JSON ({exc}). "
            f"Raw first 200 chars: {text[:200]!r}"
        )

    if outcome.get("ok"):
        return str(outcome.get("result", ""))
    err = outcome.get("error", "unknown error")
    return f"Error: {err}"
