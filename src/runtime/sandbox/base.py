"""Sandbox backend interface and data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class MountSpec:
    host_path: str
    sandbox_path: str
    mode: str = "rw"


@dataclass(frozen=True)
class ResourceLimits:
    timeout_seconds: int = 30
    max_output_chars: int = 50000
    cpus: float | None = None
    memory: str | None = None
    pids_limit: int | None = None


@dataclass(frozen=True)
class SandboxCommandRequest:
    command: str
    cwd: str
    env: dict[str, str] = field(default_factory=dict)
    mounts: list[MountSpec] = field(default_factory=list)
    network: str = "disabled"
    limits: ResourceLimits = field(default_factory=ResourceLimits)


@dataclass(frozen=True)
class SandboxCommandResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    sandbox_backend: str
    isolation: str

    def to_tool_output(self) -> str:
        output = self.stdout
        if self.stderr:
            output += f"\nSTDERR: {self.stderr}"
        if not output:
            # Distinguish "ran cleanly, produced nothing" from "exited non-zero
            # with no output" (the latter is a real failure that needs to flow
            # through the monitor as an error, not silently as success — see
            # SES01KRV1XJ7WK4177X1KHDYEWQ4B where bash returned "(no output)"
            # for everything in the sub-agent context and the model treated
            # each empty result as success and looped).
            if self.exit_code not in (0, None):
                output = f"Error: command exited with code {self.exit_code} and produced no output"
            else:
                output = "(command produced no output; exit code 0)"
        if self.timed_out:
            output += "\nError: command timed out"
        return output


class ShellSandboxBackend(Protocol):
    name: str
    isolation: str

    def run(self, request: SandboxCommandRequest) -> SandboxCommandResult:
        ...
