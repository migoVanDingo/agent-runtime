"""Host shell backend.

This preserves the legacy behavior behind the sandbox interface. It should be
used only as an explicit development fallback.
"""

from __future__ import annotations

import subprocess
import time

from runtime.sandbox.base import SandboxCommandRequest, SandboxCommandResult


class HostShellBackend:
    name = "host"
    isolation = "none"

    def run(self, request: SandboxCommandRequest) -> SandboxCommandResult:
        start = time.monotonic()
        try:
            result = subprocess.run(
                request.command,
                shell=True,
                executable="/bin/bash",
                cwd=request.cwd or None,
                env=None,
                capture_output=True,
                timeout=request.limits.timeout_seconds,
            )
            timed_out = False
            stdout = result.stdout
            stderr = result.stderr
            exit_code = result.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout = e.stdout or b""
            stderr = e.stderr or b""
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


def _clip(value: str | bytes | None, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n[truncated: output exceeded {limit} chars]"
