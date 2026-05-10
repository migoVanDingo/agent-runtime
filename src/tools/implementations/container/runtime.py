"""OCI container runtime wrapper (Docker / Podman / nerdctl / Finch)."""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

OCI_RUNTIME_PRIORITY = ("docker", "podman", "nerdctl", "finch")


def find_oci_runtime() -> str | None:
    return next((r for r in OCI_RUNTIME_PRIORITY if shutil.which(r)), None)


@dataclass
class ContainerLimits:
    timeout_seconds: float = 60.0
    memory: str = "256m"
    cpus: float = 1.0
    pids_limit: int = 64
    network: Literal["none", "bridge"] = "none"


@dataclass
class VolumeMount:
    host_path: str
    container_path: str
    mode: Literal["ro", "rw"] = "ro"


@dataclass
class ContainerResult:
    stdout: bytes
    stderr: bytes
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    runtime: str
    isolation: str = "container"


class ContainerSession:
    def __init__(self) -> None:
        self._runtime = find_oci_runtime()

    @staticmethod
    def available() -> bool:
        runtime = find_oci_runtime()
        if runtime is None:
            return False
        try:
            result = subprocess.run(
                [runtime, "info"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def run(
        self,
        image: str,
        command: str,
        mounts: list[VolumeMount] | None = None,
        limits: ContainerLimits | None = None,
        env: dict[str, str] | None = None,
    ) -> ContainerResult:
        if self._runtime is None:
            raise RuntimeError(
                "No OCI container runtime found. "
                "Install Docker Desktop, Podman Desktop, or nerdctl."
            )
        limits = limits or ContainerLimits()
        mounts = mounts or []
        env = env or {}
        cmd = self._build_command(image, command, mounts, limits, env)
        return self._run_subprocess(cmd, limits.timeout_seconds)

    def ensure_image(self, image: str) -> None:
        if self._runtime is None:
            return
        subprocess.run(
            [self._runtime, "pull", "--quiet", image],
            capture_output=True,
            timeout=120,
        )

    def _build_command(
        self,
        image: str,
        command: str,
        mounts: list[VolumeMount],
        limits: ContainerLimits,
        env: dict[str, str],
    ) -> list[str]:
        cmd = [
            self._runtime, "run", "--rm",
            "--network", limits.network,
            "--read-only",
            "--tmpfs", "/tmp:exec,size=128m",
            "--memory", limits.memory,
            f"--pids-limit={limits.pids_limit}",
            f"--cpus={limits.cpus}",
            "--security-opt", "no-new-privileges",
        ]
        for m in mounts:
            host = str(Path(m.host_path).resolve())
            cmd += ["-v", f"{host}:{m.container_path}:{m.mode}"]
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [image, "/bin/bash", "-c", command]
        return cmd

    def _run_subprocess(self, cmd: list[str], timeout: float) -> ContainerResult:
        start = time.monotonic()
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout)
            return ContainerResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                timed_out=False,
                duration_ms=int((time.monotonic() - start) * 1000),
                runtime=cmd[0],
            )
        except subprocess.TimeoutExpired as e:
            return ContainerResult(
                stdout=e.stdout or b"",
                stderr=e.stderr or b"",
                exit_code=None,
                timed_out=True,
                duration_ms=int((time.monotonic() - start) * 1000),
                runtime=cmd[0],
            )
