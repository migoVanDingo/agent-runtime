---
# 0036 — Bash Escalation: Script Execution Guard + Approval Cache

**Date**: 2026-04-18
**Status**: Planned
**Phases**: 0036a, 0036b

## Background

In session SES01KPGBW02SEP98G9ZDV5G9RDZV, the agent ran `python _tests/grok_python.py` nine times without ever asking the user for approval. The current guard (`src/runtime/guard.py`) escalates `python -c` (inline code) but silently allows `python <script_file>`. This is inconsistent — executing a script file is just as capable of arbitrary harm as inline code.

The tension: always escalating bash_exec would be unusable. Compiling a file, running grep, calling ls — these would all pause and ask. The goal is surgical escalation: ask for script execution, not for every shell command.

The approval cache solves the annoyance problem: once the user approves a specific script invocation pattern, don't ask again for the same script in the same session.

## Phase A — Script Execution Escalation

**File**: `src/runtime/guard.py`

Add a new `_SCRIPT_EXECUTION` pattern that matches running an interpreter with a file argument:

```
python[23]? <file>
bash <file>
sh <file>
node <file>
ruby <file>
perl <file>
```

Distinguishing rule: interpreter + file path (not `-c` inline, not bare interpreter). A file path argument is any non-flag token following the interpreter name that looks like a path (contains `/` or `.`).

These get ESCALATE, not BLOCK. The user may want to run the script — they just need to be asked first.

**Excluded from this pattern**: commands the guard already handles (sudo, package managers, curl|sh). Those keep their existing classification.

## Phase B — Approval Cache

**File**: `src/runtime/guard.py` (new `ApprovalCache` class), `src/agent.py` (wire up)

Once the user approves an ESCALATE decision, record the approval so identical invocations don't trigger escalation again in the same session.

**Cache key**: `(tool_name, approval_key)` where `approval_key` is:
- For `bash_exec`: the script path extracted from the command (e.g. `_tests/grok_python.py`), not the full command string. This means approving `python _tests/grok_python.py -e ...` covers `python _tests/grok_python.py -d ...` — same script, different args.
- For `delete_file`: the exact path.
- For `strace`/`ltrace`: the pid.
- For `write_file` on sensitive paths: the path.

**Scope**: Per-session (not persisted across sessions). The cache lives on the `ActionGuard` instance.

**API**:
```python
guard.record_approval(tool_name: str, approval_key: str) -> None
guard.is_approved(tool_name: str, approval_key: str) -> bool
```

**Integration in agent.py**: After `user_gate.prompt(escalation)` returns True, call `guard.record_approval(...)`. Before checking guard patterns, call `guard.is_approved(...)` and short-circuit to ALLOW if cached.

**Log**: When a cached approval is used, log `  ✓ approved (cached): {reason}` so the user can see that escalation was suppressed but not silently.

## Design Decisions

**Why script path as cache key, not full command?**
The user approves the script, not the specific arguments. If they trust `_tests/grok_python.py`, they trust all invocations of it. Requiring re-approval for each argument set would defeat the purpose of the cache.

**Why per-session only?**
Trust context changes between sessions. A script that was safe yesterday might be modified. Requiring fresh approval per session is cheap (one prompt per script per session) and safe.

**Why not always escalate bash_exec?**
Simple commands — ls, grep, cat, echo, gcc -o, python --version — would generate constant interruptions. The surgical approach (escalate interpreters+file, not all shell) preserves usability while adding meaningful oversight for the highest-risk pattern.

## Phase Summary

| Phase | What | Files |
|-------|------|-------|
| 0036a | Script execution ESCALATE patterns | guard.py |
| 0036b | Approval cache + wiring | guard.py, agent.py |
---
