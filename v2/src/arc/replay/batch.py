"""Multi-target replay scheduler (0019).

Forks one `arc replay <source> --live-llm --override-provider X --override-model Y`
subprocess per target.  Cloud + Ollama targets run concurrently; llama.cpp
targets serialize (one GPU, one model at a time) and reuse `arc llm
start`/`arc llm restart` between targets to swap models.

The batch driver doesn't itself touch the replay engine — it just shells
out to `arc replay` with the right flags.  This means the per-target
session writes go through the same code path as a single-target replay,
which keeps event-log shapes identical.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BatchTarget:
    provider: str
    model: str
    label: str = ""  # for display in the progress / compare views

    def short(self) -> str:
        return self.label or f"{self.provider}/{self.model}"


@dataclass
class BatchResult:
    target: BatchTarget
    target_session_id: str | None  # None when the subprocess failed before creating a session
    return_code: int
    elapsed_seconds: float
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.return_code == 0 and self.target_session_id is not None


@dataclass
class BatchPlan:
    """How the scheduler bucketed targets — cloud+ollama parallel, llama_cpp serial."""
    parallel: list[BatchTarget] = field(default_factory=list)
    serial: list[BatchTarget] = field(default_factory=list)


def make_plan(targets: list[BatchTarget]) -> BatchPlan:
    plan = BatchPlan()
    for t in targets:
        if t.provider == "llama_cpp":
            plan.serial.append(t)
        else:
            plan.parallel.append(t)
    return plan


def run_batch(
    *,
    source_session_id: str,
    targets: list[BatchTarget],
    arc_home: Path | None,
    max_cost_usd: float | None,
    arc_executable: list[str] | None = None,
    on_target_start=None,
    on_target_done=None,
) -> list[BatchResult]:
    """Fan out replays, return per-target BatchResult once all finish.

    `arc_executable` defaults to `[sys.executable, "-m", "arc.cli"]` so
    callers don't have to know where the entry point lives.
    `arc_home`, if set, is passed via --home to every child.
    """
    executable = arc_executable or [sys.executable, "-m", "arc.cli"]
    plan = make_plan(targets)

    results: list[BatchResult] = []

    # 1. Parallel bucket — Popen them all, wait at the end
    parallel_handles: list[tuple[BatchTarget, subprocess.Popen, float]] = []
    for t in plan.parallel:
        if on_target_start:
            on_target_start(t)
        argv = _argv_for_target(
            executable, source_session_id, t,
            arc_home=arc_home, max_cost_usd=max_cost_usd,
        )
        start_ts = time.monotonic()
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=os.environ.copy(),
            )
        except OSError as exc:
            results.append(BatchResult(
                target=t, target_session_id=None,
                return_code=2, elapsed_seconds=0.0, error=str(exc),
            ))
            if on_target_done:
                on_target_done(results[-1])
            continue
        parallel_handles.append((t, proc, start_ts))

    # 2. Serial bucket — run one at a time
    for t in plan.serial:
        if on_target_start:
            on_target_start(t)
        argv = _argv_for_target(
            executable, source_session_id, t,
            arc_home=arc_home, max_cost_usd=max_cost_usd,
        )
        start_ts = time.monotonic()
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, env=os.environ.copy(),
            )
            elapsed = time.monotonic() - start_ts
            result = BatchResult(
                target=t,
                target_session_id=_session_id_from_stdout(proc.stdout),
                return_code=proc.returncode,
                elapsed_seconds=elapsed,
                error="" if proc.returncode == 0 else proc.stdout[-500:],
            )
        except OSError as exc:
            result = BatchResult(
                target=t, target_session_id=None,
                return_code=2, elapsed_seconds=time.monotonic() - start_ts,
                error=str(exc),
            )
        results.append(result)
        if on_target_done:
            on_target_done(result)

    # 3. Wait on parallel handles
    for t, proc, start_ts in parallel_handles:
        try:
            stdout_data, _ = proc.communicate()
        except Exception as exc:
            proc.kill()
            result = BatchResult(
                target=t, target_session_id=None,
                return_code=proc.returncode if proc.returncode is not None else 2,
                elapsed_seconds=time.monotonic() - start_ts,
                error=str(exc),
            )
        else:
            text = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""
            result = BatchResult(
                target=t,
                target_session_id=_session_id_from_stdout(text),
                return_code=proc.returncode,
                elapsed_seconds=time.monotonic() - start_ts,
                error="" if proc.returncode == 0 else text[-500:],
            )
        results.append(result)
        if on_target_done:
            on_target_done(result)

    return results


# ── Helpers ────────────────────────────────────────────────────────────────


def _argv_for_target(
    executable: list[str],
    source: str,
    target: BatchTarget,
    *,
    arc_home: Path | None,
    max_cost_usd: float | None,
) -> list[str]:
    argv: list[str] = list(executable)
    if arc_home is not None:
        argv.extend(["--home", str(arc_home)])
    argv.extend([
        "replay", source,
        "--live-llm",
        "--no-diff",
        "--override-provider", target.provider,
        "--override-model", target.model,
    ])
    if max_cost_usd is not None:
        argv.extend(["--max-cost-usd", str(max_cost_usd)])
    return argv


def _session_id_from_stdout(text: str) -> str | None:
    """`_cmd_replay` prints `replaying <source> → <new>  (mode …)`; pull the new id."""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("replaying "):
            continue
        # "replaying <src> → <new>  (mode …)"
        try:
            after_arrow = line.split("→", 1)[1].strip()
        except IndexError:
            continue
        # Strip trailing " (mode …)"
        return after_arrow.split()[0]
    return None
