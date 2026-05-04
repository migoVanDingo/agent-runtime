# 0071 — Phase G: Sandbox hardening

## Goal

Fix three real problems with the current sandbox:
1. Per-call docker startup (200-500ms per command).
2. Auto-fallback to host fires under explicit `backend: docker`.
3. No macOS native sandbox backend.
4. Network policy is binary (only `none` vs not).

## Scope

- New `runtime/sandbox/session_docker.py`:
  `SessionDockerBackend` — starts one container at session begin,
  uses `docker exec` per call, teardown on close.
- New `runtime/sandbox/mac_sandbox.py`:
  `MacSandboxExecBackend` — wraps `sandbox-exec(1)` on macOS with
  a generated profile pinned to workspace.
- Add `backend: auto` resolution in `SandboxManager`:
  - auto → docker (if available) → mac_sandbox_exec (if macOS) → host + WARN.
  - `backend: docker` → fail fast on infra failure (no auto-fallback).
- Add network policy `outbound` (no `--network` flag) alongside `none`.
- Config default: `backend: auto` (was `docker`).

## Files touched

`runtime/sandbox/session_docker.py` (new), `runtime/sandbox/mac_sandbox.py` (new),
`runtime/sandbox/manager.py`, `config.py`, `config.yml`.

## Exit criteria

- `backend: auto` is the new config default.
- `backend: docker` with simulated infra failure raises RuntimeError.
- Tests: auto backend resolution mocked for 3 environments.
- Tests: mac sandbox exec wraps sandbox-exec correctly.
