"""Session-scoped Docker sandbox backend.

Starts one container when `start()` is called and executes subsequent
shell commands via `docker exec`. Dramatically faster than per-call
`docker run` (no container startup latency per command).
"""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

from runtime.sandbox.base import MountSpec, SandboxCommandRequest, SandboxCommandResult


class SessionDockerBackend:
    """One container per session; execute via `docker exec`."""

    name = "session_docker"
    isolation = "container"

    def __init__(self, image: str) -> None:
        self._image = image
        self._container_id: str | None = None

    def available(self) -> bool:
        return shutil.which("docker") is not None

    def start(self, workspace: str) -> None:
        """Start the long-lived container. Call once at session start."""
        if not self.available():
            raise RuntimeError("docker executable not found")
        container_name = f"agent-sandbox-{uuid.uuid4().hex[:8]}"
        workspace_path = str(Path(workspace).resolve())
        cmd = [
            "docker", "run", "-d", "--rm",
            "--name", container_name,
            "--network", "none",
            "--workdir", "/workspace",
            "-v", f"{workspace_path}:/workspace:rw",
            "--tmpfs", "/tmp",
            self._image,
            "tail", "-f", "/dev/null",  # keep container alive
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to start sandbox container: {result.stderr.strip()}"
            )
        self._container_id = result.stdout.strip()

    def run(self, request: SandboxCommandRequest) -> SandboxCommandResult:
        if self._container_id is None:
            raise RuntimeError("SessionDockerBackend: container not started. Call start() first.")

        network_flag = request.network == "disabled"
        # Note: network is set at container-start time for session docker.
        # Per-call network changes are not supported in this backend.

        exec_cmd = [
            "docker", "exec",
            "-w", "/workspace",
            self._container_id,
            "/bin/bash", "-lc", request.command,
        ]
        start = time.monotonic()
        try:
            result = subprocess.run(
                exec_cmd,
                capture_output=True,
                text=True,
                timeout=request.limits.timeout_seconds,
            )
            timed_out = False
            stdout = result.stdout
            stderr = result.stderr
            exit_code = result.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout = e.stdout or ""
            stderr = e.stderr or ""
            exit_code = None

        duration_ms = int((time.monotonic() - start) * 1000)
        return SandboxCommandResult(
            stdout=_clip(stdout, request.limits.max_output_chars),
            stderr=_clip(stderr, request.limits.max_output_chars),
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            sandbox_backend=self.name,
            isolation=self.isolation,
        )

    def stop(self) -> None:
        """Stop the container. Call at session end."""
        if self._container_id is None:
            return
        try:
            subprocess.run(
                ["docker", "stop", self._container_id],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass
        finally:
            self._container_id = None

    def __del__(self):
        self.stop()


def _clip(value: str | bytes | None, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n[truncated: output exceeded {limit} chars]"
