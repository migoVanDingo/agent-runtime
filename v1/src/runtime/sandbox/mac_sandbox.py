"""macOS sandbox-exec backend.

Uses the built-in `sandbox-exec(1)` tool with a generated sandbox profile
that restricts file access to the workspace directory. Requires macOS only.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from runtime.sandbox.base import SandboxCommandRequest, SandboxCommandResult


_PROFILE_TEMPLATE = """\
(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow file-read* (subpath "{workspace}"))
(allow file-write* (subpath "{workspace}"))
(allow file-read* (subpath "/tmp"))
(allow file-write* (subpath "/tmp"))
(allow file-read* (subpath "/private/tmp"))
(allow file-write* (subpath "/private/tmp"))
(allow file-read* (literal "/dev/null"))
(allow file-write* (literal "/dev/null"))
(allow file-read* (subpath "/usr"))
(allow file-read* (subpath "/bin"))
(allow file-read* (subpath "/sbin"))
(allow file-read* (subpath "/System"))
(allow file-read* (subpath "/Library/Developer"))
(allow file-read* (subpath "/Library/Preferences"))
(allow file-read* (subpath "/opt/homebrew"))
(allow mach-lookup)
(allow sysctl-read)
"""


class MacSandboxExecBackend:
    """macOS sandbox-exec wrapper."""

    name = "mac_sandbox_exec"
    isolation = "sandbox_exec"

    def __init__(self, workspace: str = ".") -> None:
        self._workspace = str(Path(workspace).resolve())

    @staticmethod
    def available() -> bool:
        return platform.system() == "Darwin" and shutil.which("sandbox-exec") is not None

    def run(self, request: SandboxCommandRequest) -> SandboxCommandResult:
        workspace = str(Path(request.cwd or self._workspace).resolve())
        profile = _PROFILE_TEMPLATE.format(workspace=workspace)

        # Write profile to a temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sb", delete=False, prefix="agent-sandbox-"
        ) as tf:
            tf.write(profile)
            profile_path = tf.name

        try:
            cmd = [
                "sandbox-exec", "-f", profile_path,
                "/bin/bash", "-lc", request.command,
            ]
            start = time.monotonic()
            try:
                result = subprocess.run(
                    cmd,
                    cwd=workspace,
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
        finally:
            import os
            try:
                os.unlink(profile_path)
            except OSError:
                pass

        return SandboxCommandResult(
            stdout=_clip(stdout, request.limits.max_output_chars),
            stderr=_clip(stderr, request.limits.max_output_chars),
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            sandbox_backend=self.name,
            isolation=self.isolation,
        )


def _clip(value: str | bytes | None, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n[truncated: output exceeded {limit} chars]"
