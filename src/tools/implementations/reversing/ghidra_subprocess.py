"""Subprocess worker for Ghidra operations — runs on its own OS main thread.

Why this exists: the JVM (specifically Ghidra in headless mode on macOS) has a
hard requirement that JNI work runs on the OS main thread. Python's
``threading.Thread`` is a pthread, not the OS main thread. When the agent
runs on a worker thread (e.g., via ``InProcessAgentService``'s
``ThreadPoolExecutor``), any ``pyghidra`` call hangs indefinitely. Verified
across multiple sessions — every TUI session that touched Ghidra prior to
this rewrite hung.

The fix is to spawn this script as a child Python process. The subprocess
has its own OS main thread, runs ``pyghidra`` from it (= works), prints
JSON to stdout, and exits. The agent process treats Ghidra exactly like
any other external program — spawn, wait, kill on timeout, parse output.

Protocol:
    invocation:  python -m tools.implementations.reversing.ghidra_subprocess
                 --binary <path> --op <name> [--params <json>]
                 [--project-location <dir>] [--project-name <name>]
                 [--ghidra-home <dir>]
    stdout:      a single JSON object: {"ok": bool, "result": str, "error": str}
    stderr:      JVM noise + ``PROGRESS:`` lines the parent can stream as
                 telemetry events (one event per line).

Cancellation:
    The parent owns the subprocess lifecycle and can kill it at any time
    (``subprocess.Popen.terminate`` then ``kill``). The runtime's
    ``_pause_check`` callback wraps the wait loop in ``ghidra_cache``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path


def _emit_progress(message: str) -> None:
    """Write a progress line to stderr. Parent streams these as events."""
    print(f"PROGRESS: {message}", file=sys.stderr, flush=True)


def _run(args: argparse.Namespace) -> dict:
    # Import lazily so --help / argument errors don't pay JVM startup cost.
    import pyghidra

    ghidra_home = args.ghidra_home or os.environ.get("GHIDRA_HOME")
    if not ghidra_home:
        return {
            "ok": False,
            "error": "GHIDRA_HOME not set. Pass --ghidra-home or set the env var.",
        }

    _emit_progress(f"starting JVM (GHIDRA_HOME={ghidra_home})")
    pyghidra.start(install_dir=ghidra_home, verbose=False)
    _emit_progress("JVM ready")

    # Operations registry. Imported AFTER pyghidra.start so the operations
    # can use Java types lazily.
    from tools.implementations.reversing.ghidra_ops import OPERATIONS, known_operations

    if args.op not in OPERATIONS:
        return {
            "ok": False,
            "error": f"unknown operation {args.op!r}. Known: {known_operations()}",
        }

    op_fn = OPERATIONS[args.op]
    params = json.loads(args.params) if args.params else {}
    # Always make the binary path available to the operation.
    params.setdefault("path", args.binary)

    project_location = args.project_location
    project_name = args.project_name or (Path(args.binary).stem + "_ghidra")

    _emit_progress(f"opening program {args.binary}")
    with pyghidra.open_program(
        args.binary,
        project_location=project_location,
        project_name=project_name,
        analyze=True,
    ) as api:
        _emit_progress(f"program loaded, dispatching op={args.op}")
        result = op_fn(api, params)
    _emit_progress("op completed")
    return {"ok": True, "result": result}


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ghidra_subprocess",
        description="Run one Ghidra operation in an isolated process.",
    )
    parser.add_argument("--binary", required=True, help="Path to the binary")
    parser.add_argument("--op", required=True, help="Operation name (see ghidra_ops.OPERATIONS)")
    parser.add_argument("--params", default="", help="JSON-encoded params dict")
    parser.add_argument("--project-location", default=None, help="Ghidra project root directory")
    parser.add_argument("--project-name", default=None, help="Ghidra project name")
    parser.add_argument("--ghidra-home", default=None, help="Override GHIDRA_HOME env var")
    args = parser.parse_args()

    try:
        outcome = _run(args)
    except Exception as exc:
        outcome = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }

    # JSON-only on stdout. Anything else goes to stderr.
    print(json.dumps(outcome, ensure_ascii=False, default=str), flush=True)
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
