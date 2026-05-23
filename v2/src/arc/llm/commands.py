"""High-level `arc llm` command implementations.

Each function corresponds to one subcommand; the CLI parser in `cli.py`
calls these.  The picker in `arc/setup/picker.py` (0017) also imports
`start` / `status` / `stop` directly for inline server management.

See _design/0018-llm-server-lifecycle.md.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from arc.bootstrap import HomePaths
from arc.llm.process import (
    StartResult,
    read_pid_file,
    start as _proc_start,
    status as _proc_status,
    stop as _proc_stop,
    tail_log,
)
from arc.llm.registry import (
    Registry,
    RegistryError,
    ServerModel,
    load_registry,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _registry(paths: HomePaths) -> Registry:
    return load_registry(paths.llm_servers_file)


def _base_url_from_args(args: list[str]) -> str:
    """Extract --host + --port from a flat arg list; default to localhost:8080."""
    host = "127.0.0.1"
    port = "8080"
    it = iter(args)
    for token in it:
        if token == "--host":
            host = next(it, host)
        elif token == "--port":
            port = next(it, port)
    return f"http://{host}:{port}/v1"


# ── List ───────────────────────────────────────────────────────────────────


def list_models(paths: HomePaths) -> int:
    """Render all registered models + which (if any) is running."""
    reg = _registry(paths)
    running = _proc_status(llm_dir=paths.llm_dir)
    running_id = running.model_id if running else None

    if not reg.models:
        print(f"no models registered in {reg.source_path}")
        print("edit the file to add entries (see comments in the file for shape).")
        return 0

    # Compute column widths
    id_w = max(len(m.id) for m in reg.models)
    label_w = max(len(m.label) for m in reg.models)
    print(f"{'ID':{id_w}}  {'LABEL':{label_w}}  STATUS")
    for m in reg.models:
        if m.id == running_id and running is not None:
            uptime = _format_uptime(running.started_dt)
            status = f"running (pid {running.pid}, {uptime})"
        else:
            status = "available"
        print(f"{m.id:{id_w}}  {m.label:{label_w}}  {status}")
    return 0


# ── Status ─────────────────────────────────────────────────────────────────


def show_status(paths: HomePaths) -> int:
    state = _proc_status(llm_dir=paths.llm_dir)
    if state is None:
        print("no llama-server process tracked by arc.")
        print(f"  (run `arc llm start <model-id>` to launch one; ")
        print(f"   to see registered models: `arc llm list`)")
        return 0

    reg = _registry(paths)
    base_url = _base_url_from_args(reg.default_args)
    print(f"Running:  {state.model_id}")
    print(f"PID:      {state.pid}")
    print(f"Started:  {state.started_at}  ({_format_uptime(state.started_dt)} ago)")
    print(f"Listening:{base_url}")

    # Also probe /health for live status
    from arc.llm.health import wait_for_healthy
    healthy = wait_for_healthy(base_url=base_url, timeout_seconds=2, poll_seconds=0.1)
    print(f"/health:  {'ok' if healthy else 'not responding'}")
    return 0


# ── Start ──────────────────────────────────────────────────────────────────


def start_server(paths: HomePaths, model_id: str, *, on_progress=None) -> int:
    """Start the server for the named model.  Errors if a *different* model
    is already running (use `arc llm restart` to swap)."""
    reg = _registry(paths)
    try:
        model = reg.find(model_id)
    except RegistryError as e:
        print(str(e), file=sys.stderr)
        return 2

    # Same model already running? no-op
    existing = _proc_status(llm_dir=paths.llm_dir)
    if existing is not None and existing.model_id == model_id:
        print(f"already running: {model_id} (pid {existing.pid})")
        return 0
    if existing is not None and existing.model_id != model_id:
        print(
            f"another model is running: {existing.model_id} (pid {existing.pid}).\n"
            f"  run `arc llm stop` first or `arc llm restart {model_id}` to swap.",
            file=sys.stderr,
        )
        return 1

    # Verify the gguf file exists before spawning so we fail fast
    if not model.gguf.exists():
        print(
            f"gguf file not found: {model.gguf}\n"
            f"  edit ~/.arc/llm_servers.yml or download the file.",
            file=sys.stderr,
        )
        return 2

    argv = reg.build_argv(model)
    base_url = _base_url_from_args(reg.default_args)

    print(f"Starting {model.id}…")
    print(f"  cmd: {' '.join(argv)}")

    def _default_progress(elapsed: float, status: str):
        sys.stdout.write(f"\r  waiting for /health: {status:<22}  {elapsed:5.0f}s elapsed")
        sys.stdout.flush()

    cb = on_progress if on_progress is not None else _default_progress

    try:
        result: StartResult = _proc_start(
            llm_dir=paths.llm_dir,
            argv=argv,
            model_id=model.id,
            base_url=base_url,
            startup_timeout_seconds=reg.startup_timeout_seconds,
            progress_cb=cb,
        )
    except Exception as e:
        print(f"\nfailed to start: {e}", file=sys.stderr)
        log = tail_log(llm_dir=paths.llm_dir, n=20)
        if log:
            print("--- last 20 log lines ---", file=sys.stderr)
            print(log, file=sys.stderr)
        return 1

    print()  # newline after progress line
    if result.health_ok:
        print(f"ready (took {result.health_elapsed_seconds:.0f}s).")
    else:
        print(
            f"server spawned but /health didn't return ok within "
            f"{reg.startup_timeout_seconds}s — it may still be loading."
        )
        print(f"check `arc llm logs --tail 30` for progress.")
    print(f"logs: {paths.llm_dir}/current.log")
    return 0 if result.health_ok else 1


# ── Stop ───────────────────────────────────────────────────────────────────


def stop_server(paths: HomePaths) -> int:
    state = _proc_status(llm_dir=paths.llm_dir)
    if state is None:
        print("no server running.")
        return 0
    print(f"sending SIGTERM to pid {state.pid} ({state.model_id})…")
    stopped = _proc_stop(llm_dir=paths.llm_dir)
    if stopped:
        print("stopped.")
    else:
        print("nothing to stop.")
    return 0


# ── Restart ────────────────────────────────────────────────────────────────


def restart_server(paths: HomePaths, model_id: str) -> int:
    state = _proc_status(llm_dir=paths.llm_dir)
    if state is not None:
        if state.model_id == model_id:
            print(f"already running: {model_id} — no-op.")
            return 0
        print(f"stopping current ({state.model_id})…")
        _proc_stop(llm_dir=paths.llm_dir)
    return start_server(paths, model_id)


# ── Logs ───────────────────────────────────────────────────────────────────


def show_logs(paths: HomePaths, *, tail: int) -> int:
    text = tail_log(llm_dir=paths.llm_dir, n=tail)
    if not text:
        print(f"no log at {paths.llm_dir}/current.log")
        return 0
    print(text)
    return 0


# ── Display helpers ───────────────────────────────────────────────────────


def _format_uptime(started: datetime) -> str:
    now = datetime.now(timezone.utc)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    delta = now - started
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h{minutes % 60:02d}m"
