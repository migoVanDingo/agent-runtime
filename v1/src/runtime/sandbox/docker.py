"""Docker shell sandbox backend."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from runtime.sandbox.base import MountSpec, SandboxCommandRequest, SandboxCommandResult


class DockerShellBackend:
    name = "docker"
    isolation = "container"

    def __init__(self, image: str) -> None:
        self._image = image

    def available(self) -> bool:
        return shutil.which("docker") is not None

    def run(self, request: SandboxCommandRequest) -> SandboxCommandResult:
        if not self.available():
            raise RuntimeError("docker executable not found")

        start = time.monotonic()
        cmd = self._build_command(request)
        try:
            result = subprocess.run(
                cmd,
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

    def _build_command(self, request: SandboxCommandRequest) -> list[str]:
        mounts = request.mounts or [
            MountSpec(host_path=str(Path(request.cwd).resolve()), sandbox_path="/workspace", mode="rw")
        ]
        cmd = ["docker", "run", "--rm", "--user", _docker_user(), "--workdir", "/workspace"]

        if request.network == "disabled":
            cmd.extend(["--network", "none"])

        if request.limits.cpus is not None:
            cmd.extend(["--cpus", str(request.limits.cpus)])
        if request.limits.memory:
            cmd.extend(["--memory", request.limits.memory])
        if request.limits.pids_limit is not None:
            cmd.extend(["--pids-limit", str(request.limits.pids_limit)])

        cmd.extend(["--tmpfs", "/tmp"])

        for mount in mounts:
            host = str(Path(mount.host_path).resolve())
            cmd.extend(["-v", f"{host}:{mount.sandbox_path}:{mount.mode}"])

        for key, value in request.env.items():
            cmd.extend(["-e", f"{key}={value}"])

        cmd.extend([self._image, "/bin/bash", "-lc", request.command])
        return cmd


def _docker_user() -> str:
    try:
        return f"{os.getuid()}:{os.getgid()}"
    except AttributeError:
        return "1000:1000"


def _clip(value: str | bytes | None, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n[truncated: output exceeded {limit} chars]"
