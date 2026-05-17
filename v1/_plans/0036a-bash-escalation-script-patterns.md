# 0036a — Bash Escalation: Script Execution Patterns

**Date**: 2026-04-18
**Status**: Implemented
**Parent**: 0036

## Problem

`python <script.py>` was silently ALLOW in the guard. Only `python -c`
(inline code) triggered ESCALATE. Running a script file is equally capable
of arbitrary harm — the agent ran `grok_python.py` nine times in a row
without ever asking the user.

## Fix

Added `_SCRIPT_EXECUTION` regex to `guard.py`:

```
interpreter + whitespace + non-flag + file-like token
```

Where interpreter is: `python[23]?`, `bash`, `sh`, `zsh`, `node`, `ruby`, `perl`.

A "file-like token" must either have an extension (`.py`, `.sh`, `.js`,
`.rb`, `.pl`, `.ts`) or contain a path separator (`/`). This distinguishes
`python myscript.py` (ESCALATE) from `python --version` (ALLOW) and
`python -m module` (ALLOW).

Added to `_check_shell_command` after the existing `_CODE_EXECUTION` check,
returning `ESCALATE` with reason `"script execution: '<matched>'"`.

## Changes

- `src/runtime/guard.py` — `_SCRIPT_EXECUTION` pattern, check in `_check_shell_command`
