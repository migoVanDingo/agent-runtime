"""Sandbox backend selection and command request construction."""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

from logger import get_logger
from runtime.sandbox.base import MountSpec, ResourceLimits, SandboxCommandRequest, SandboxCommandResult
from runtime.sandbox.host import HostShellBackend

logger = get_logger(__name__)


class SandboxManager:
    """Resolves which backend to use and dispatches shell commands.

    Backend resolution order for `backend: auto`:
        1. MacSandboxExec (if on macOS and `sandbox-exec` is present)
        2. Host (development fallback, no isolation)

    `backend: host`          → direct host execution (explicit opt-in).
    `backend: mac_sandbox_exec` → macOS sandbox-exec profile (recommended).
    `backend: auto`          → mac_sandbox_exec if available, else host.

    Docker is intentionally excluded from bash_exec sandboxing.
    It is reserved for the containerized dynamic-analysis toolset.
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
                "timed_out": result.timed_out,
                "network": self._cfg.default_network,
            },
            duration_ms=result.duration_ms,
            stage="SandboxManager",
        ))
        if result.timed_out or result.exit_code in (137, 124):
            bus.emit(RuntimeEvent(
                "tool.call.resource_limit",
                identity,
                payload={
                    "resource": "memory" if result.exit_code == 137 else "timeout",
                    "backend": result.sandbox_backend,
                    "exit_code": result.exit_code,
                    "observed": self._cfg.command_timeout_seconds,
                },
                severity="warn",
                stage="SandboxManager",
            ))
        return result

    def _dispatch(self, backend_name: str, request: SandboxCommandRequest) -> SandboxCommandResult:
        if backend_name == "auto":
            return self._run_auto(request)

        if backend_name in ("mac_sandbox_exec", "sandbox_exec"):
            return self._run_mac_sandbox(request)

        if backend_name == "host":
            return self._run_host(request)

        raise RuntimeError(f"unknown sandbox backend: {backend_name!r}")

    def _run_auto(self, request: SandboxCommandRequest) -> SandboxCommandResult:
        """Graceful backend resolution: mac_sandbox_exec → host."""
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
        logger.debug("sandbox: using host backend with no process isolation")
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
