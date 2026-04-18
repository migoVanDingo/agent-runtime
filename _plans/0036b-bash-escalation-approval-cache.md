# 0036b — Bash Escalation: Approval Cache

**Date**: 2026-04-18
**Status**: Implemented
**Parent**: 0036

## Problem

With script execution now escalating, the 9-retry loop from session
SES01KPGBW02SEP98G9ZDV5G9RDZV would have prompted the user 9 times for the
same script. That's worse than not escalating at all.

## Fix

`ActionGuard` now maintains a per-session `_approved: set[str]` cache.

**Cache key** (`_approval_key` function): identifies the approval intent,
not the exact invocation:
- `bash_exec` script calls → `bash_exec:script:<path>` — same script, any args
- `bash_exec` other → `bash_exec:<full_command>`
- `delete_file` → `delete_file:<path>`
- `strace`/`ltrace` → `tool:pid:<pid>`
- `write_file` sensitive path → `write_file:<path>`

**Flow**:
1. `check_tool_call()` checks cache first — if key is in `_approved`, returns ALLOW with log `✓ approved (cached): <key>`
2. If ESCALATE fires and user says yes → `record_approval()` is called → key added to cache
3. Subsequent calls with same key skip the prompt entirely

**Scope**: Per `ActionGuard` instance (per session). Not persisted.

## User experience

First `python _tests/grok_python.py -e ...` → user prompted → approves.
All subsequent `python _tests/grok_python.py <any args>` → silent ALLOW with cache log.

## Changes

- `src/runtime/guard.py` — `_approval_key()`, `ActionGuard.__init__`, `record_approval()`, cache check in `check_tool_call()`
- `src/agent.py` — calls `guard.record_approval()` after user approves in both `_run_step` and `_run_loop`
