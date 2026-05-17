# 0057 - Runtime Refactor Phase 2: Sandboxed Bash

## Goal

Move `bash_exec` behind a sandbox abstraction so command execution no longer lives directly in the shell tool implementation.

## Implemented

- Added `runtime.sandbox` package:
  - `SandboxCommandRequest`
  - `SandboxCommandResult`
  - `MountSpec`
  - `ResourceLimits`
  - `ShellSandboxBackend`
  - `SandboxManager`
  - `HostShellBackend`
  - `DockerShellBackend`
- Updated `BashExecTool` to call `SandboxManager().run_shell(command)`.
- Added sandbox config:
  - backend
  - host fallback toggle
  - Docker image
  - network default
  - timeout
  - output limit
  - workspace root
  - allowed roots placeholders
- Changed default sandbox backend to `docker` with explicit host fallback for local development.
- Added unit coverage for the host backend behavior.

## Behavior Notes

The tool no longer owns `subprocess.run(..., shell=True)`. That behavior is isolated in `HostShellBackend`, which is now an explicit backend rather than the core implementation.

Docker backend behavior:

- mounts the configured workspace at `/workspace`,
- uses `/bin/bash -lc`,
- disables network when configured,
- supports basic resource-limit fields,
- captures stdout/stderr/exit code/timeout.

If Docker is unavailable and `allow_host_backend=true`, the manager falls back to host execution and prepends a sandbox warning to stdout. For multi-user mode, host fallback should be disabled.

## Remaining Work

- Enforce path policy for file tools and shell mount decisions.
- Add structured sandbox events.
- Add stronger container options after validating local Docker compatibility.
- Add network approval integration.

## Verification

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src
```
