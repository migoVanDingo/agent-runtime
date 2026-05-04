"""Sandbox backend selection and command request construction."""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

from logger import get_logger
from runtime.sandbox.base import MountSpec, ResourceLimits, SandboxCommandRequest, SandboxCommandResult
from runtime.sandbox.docker import DockerShellBackend
from runtime.sandbox.host import HostShellBackend

logger = get_logger(__name__)


class SandboxManager:
    """Resolves which backend to use and dispatches shell commands.

    Backend resolution order for `backend: auto`:
        1. Docker (if `docker` executable is present)
        2. MacSandboxExec (if on macOS and `sandbox-exec` is present)
        3. Host (with a loud warning)

    `backend: docker` → fails fast on Docker infrastructure errors (no fallback).
    `backend: host`   → host execution with a warning.
    `backend: auto`   → as above with graceful degradation.
    """

    def __init__(self, sandbox_config=None) -> None:
        if sandbox_config is None:
            from app_config import config
            sandbox_config = config.runtime.sandbox
        self._cfg = sandbox_config

    def run_shell(self, command: str, *, cwd: str | None = None) -> SandboxCommandResult:
        from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
        identity = get_runtime_identity()
        bus = get_event_bus()

        cwd_path = Path(cwd or ".").resolve()
        workspace = Path(self._cfg.workspace_root or ".").resolve()
        request = SandboxCommandRequest(
            command=command,
            cwd=str(cwd_path),
            mounts=[MountSpec(str(workspace), "/workspace", "rw")],
            network=self._cfg.default_network,
            limits=ResourceLimits(
                timeout_seconds=self._cfg.command_timeout_seconds,
                max_output_chars=self._cfg.max_output_chars,
            ),
        )

        backend_name = (self._cfg.backend or "auto").lower()
        result = self._dispatch(backend_name, request)

        bus.emit(RuntimeEvent(
            "sandbox.run", identity,
            payload={
                "backend": result.sandbox_backend,
                "isolation": result.isolation,
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "timed_out": result.timed_out,
                "network": self._cfg.default_network,
            },
            stage="SandboxManager",
        ))
        return result

    def _dispatch(self, backend_name: str, request: SandboxCommandRequest) -> SandboxCommandResult:
        if backend_name == "docker":
            return self._run_docker(request, allow_host_fallback=False)

        if backend_name == "auto":
            return self._run_auto(request)

        if backend_name in ("mac_sandbox_exec", "sandbox_exec"):
            return self._run_mac_sandbox(request)

        if backend_name == "host":
            return self._run_host(request)

        raise RuntimeError(f"unknown sandbox backend: {backend_name!r}")

    def _run_docker(self, request: SandboxCommandRequest, allow_host_fallback: bool) -> SandboxCommandResult:
        docker = DockerShellBackend(self._cfg.docker_image)
        try:
            result = docker.run(request)
            if _is_docker_infrastructure_failure(result):
                raise RuntimeError(
                    (result.stderr or result.stdout).strip() or "docker infrastructure failure"
                )
            return result
        except Exception as e:
            if not allow_host_fallback:
                raise RuntimeError(
                    f"sandbox: docker backend failed and host fallback is disabled: {e}"
                ) from e
            logger.warning(f"sandbox: docker unavailable ({e}); falling back to host backend")
            result = HostShellBackend().run(request)
            return _with_fallback_warning(result, str(e))

    def _run_auto(self, request: SandboxCommandRequest) -> SandboxCommandResult:
        """Graceful backend resolution: docker → mac_sandbox_exec → host."""
        if shutil.which("docker"):
            try:
                return self._run_docker(request, allow_host_fallback=False)
            except Exception as e:
                logger.warning(f"sandbox: auto — docker failed ({e}), trying next backend")

        if platform.system() == "Darwin" and shutil.which("sandbox-exec"):
            try:
                return self._run_mac_sandbox(request)
            except Exception as e:
                logger.warning(f"sandbox: auto — mac_sandbox_exec failed ({e}), falling back to host")

        logger.warning("sandbox: auto — no sandboxed backend available; using host (no isolation)")
        result = HostShellBackend().run(request)
        return _with_fallback_warning(result, "no sandboxed backend available")

    def _run_mac_sandbox(self, request: SandboxCommandRequest) -> SandboxCommandResult:
        from runtime.sandbox.mac_sandbox import MacSandboxExecBackend
        backend = MacSandboxExecBackend(workspace=request.cwd)
        return backend.run(request)

    def _run_host(self, request: SandboxCommandRequest) -> SandboxCommandResult:
        if not self._cfg.allow_host_backend:
            raise RuntimeError("host sandbox backend is disabled by config")
        logger.warning("sandbox: using host backend with no process isolation")
        return HostShellBackend().run(request)


def _with_fallback_warning(result: SandboxCommandResult, reason: str) -> SandboxCommandResult:
    warning = f"[sandbox warning: sandboxed backend unavailable; ran on host: {reason}]\n"
    return SandboxCommandResult(
        stdout=warning + result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration_ms=result.duration_ms,
        sandbox_backend=result.sandbox_backend,
        isolation=result.isolation,
    )


def _is_docker_infrastructure_failure(result: SandboxCommandResult) -> bool:
    if result.sandbox_backend != "docker" or result.exit_code == 0:
        return False
    text = f"{result.stdout}\n{result.stderr}".lower()
    signals = (
        "cannot connect to the docker daemon",
        "permission denied while trying to connect to the docker",
        "is the docker daemon running",
        "docker: command not found",
    )
    return any(s in text for s in signals)
