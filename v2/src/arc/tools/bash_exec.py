"""bash_exec — execute a shell command via subprocess.

Host backend only in phase 2.1. The guard plugin is the only safety layer;
sandboxing (sandbox-exec, container, etc.) is a future plugin not in scope.

All tunable values come from `tools.config.bash_exec`:
  timeout_seconds      hard wall-clock cap; subprocess killed after
  max_output_chars     truncate combined stdout+stderr at this length
  working_directory    default cwd; null = inherit runtime.workspace

Per-call overrides via tool input: `cwd`, `timeout_seconds`.
"""
from __future__ import annotations

import os
import subprocess

from arc.tools.base import ToolError, ToolInputSchema


class BashExecTool:
    """Run a shell command. Returns combined stdout+stderr.

    Errors (non-zero exit, timeout) are prefixed with `Error:` and returned
    as the tool output — the loop's loop emits tool.call.completed with
    ok=True (the tool ran successfully even though the command failed).
    The model sees the error text and can decide what to do.

    Truly broken cases (subprocess can't even start) raise ToolError; the
    loop emits tool.call.failed.
    """

    name = "bash_exec"
    description = (
        "Execute a bash command. Returns combined stdout and stderr. "
        "Supports pipes, redirects, heredocs. Each call is a fresh subprocess "
        "(no shell state persists between calls). Use this for filesystem "
        "operations, running scripts, inspecting processes, etc."
    )

    def __init__(
        self,
        *,
        timeout_seconds: int,
        max_output_chars: int,
        working_directory: str | None,
    ) -> None:
        self._default_timeout = timeout_seconds
        self._max_chars = max_output_chars
        self._default_cwd = working_directory  # may be None → use os.getcwd()

    @classmethod
    def from_config(cls, cfg: dict) -> "BashExecTool":
        try:
            return cls(
                timeout_seconds=int(cfg["timeout_seconds"]),
                max_output_chars=int(cfg["max_output_chars"]),
                working_directory=cfg.get("working_directory"),
            )
        except KeyError as e:
            raise ValueError(f"tools.config.bash_exec missing required key: {e.args[0]!r}")

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            properties={
                "command": {
                    "type": "string",
                    "description": "The bash command to run. Shell metacharacters allowed.",
                },
                "cwd": {
                    "type": "string",
                    "description": (
                        "Working directory for this call. "
                        "Defaults to the configured workspace."
                    ),
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": (
                        f"Per-call timeout override. "
                        f"Defaults to {self._default_timeout}s."
                    ),
                },
            },
            required=["command"],
        )

    def execute(self, input: dict) -> str:
        command = input.get("command", "")
        if not isinstance(command, str) or not command.strip():
            raise ToolError("bash_exec requires a non-empty 'command' string")

        cwd = input.get("cwd") or self._default_cwd or os.getcwd()
        timeout = int(input.get("timeout_seconds") or self._default_timeout)

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as e:
            # cwd doesn't exist, or shell can't be found
            raise ToolError(f"could not run command: {e}")
        except subprocess.TimeoutExpired as e:
            partial = self._format_output(
                stdout=e.stdout or "",
                stderr=e.stderr or "",
                exit_code=None,
                timed_out=True,
                timeout=timeout,
            )
            return partial

        return self._format_output(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            timed_out=False,
            timeout=timeout,
        )

    def _format_output(
        self,
        *,
        stdout: str,
        stderr: str,
        exit_code: int | None,
        timed_out: bool,
        timeout: int,
    ) -> str:
        # Decode bytes if needed (text=True should give str; guard anyway)
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")

        body = stdout
        if stderr:
            body = (body + "\nSTDERR: " + stderr) if body else f"STDERR: {stderr}"

        # Distinguish "ran cleanly with no output" from "failed with no output"
        if not body:
            if timed_out:
                body = ""  # we'll add the timeout marker below
            elif exit_code not in (0, None):
                body = f"Error: command exited with code {exit_code} and produced no output"
            else:
                body = "(command produced no output; exit code 0)"

        # Truncate to max chars before adding trailer info
        if len(body) > self._max_chars:
            body = body[: self._max_chars] + (
                f"\n[truncated; original was {len(body)} chars]"
            )

        if timed_out:
            body = (body + "\n" if body else "") + f"Error: command timed out after {timeout}s"
        elif exit_code not in (0, None):
            # Prepend exit code marker so model knows the command failed
            body = f"Error: exit code {exit_code}\n{body}"

        return body
