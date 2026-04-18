# 0034a — Phase 1: Safety & Correctness

**Date**: 2026-04-17
**Status**: Implemented
**Parent**: 0034

## Changes

### 1a. Real ESCALATE: User-in-the-Loop

**New file**: `src/runtime/escalation.py`

- `Escalation` dataclass: `reason`, `source` (guard|monitor|critic), `tool_name`, `tool_input`
- `UserGate` protocol: single method `prompt(escalation) -> bool`
- `CLIUserGate`: prints formatted escalation to stdout, reads y/n from stdin
- `AutoDenyGate`: always denies (for headless/testing)

**Modified**: `src/agent.py`

- `Agent.__init__` accepts optional `user_gate` parameter (defaults to `CLIUserGate()`)
- **Step-level guard ESCALATE** (plan mode): creates Escalation, stops spinner, calls `user_gate.prompt()`. If approved → executes step. If denied → sets `step.error = "user denied escalation"`.
- **Monitor ESCALATE** (plan mode): same flow. If denied → marks step as skipped.
- **Tool-call-level ESCALATE** (both modes): creates Escalation with tool_name and tool_input, prompts user. If approved → executes tool. If denied → returns denial string to model.

### 1b. Argument-Level Guard for bash_exec

**Modified**: `src/runtime/guard.py`

Added two new regex patterns:

- `_PACKAGE_MANAGERS`: catches `brew install/uninstall`, `pip install/uninstall`, `apt-get install/remove`, `npm install -g`, `yarn global add`, `gem install`, `cargo install`, `go install` → **ESCALATE** (not BLOCK — user might genuinely want it)
- `_CODE_EXECUTION`: catches `python -c`, `ruby -e`, `perl -e`, `node -e` → **ESCALATE**

Wired into `_check_shell_command()` between the sudo check and the sensitive path check.

**Before**: `brew install checksec` → ALLOW (not caught by any pattern)
**After**: `brew install checksec` → ESCALATE ("package manager operation: 'brew install'")

### 1c. Fix Critic Fallback

**Modified**: `src/agent.py`

**Before**: If planner revision failed or was invalid, used the original (criticized) plan.
**After**: Calls `_strip_challenged_steps()`:
- Steps with suggestion `"drop"` → removed
- Steps with suggestion `"replace"` → removed (can't auto-replace without valid revision)
- Steps with suggestion `"justify"` → kept (benefit of the doubt)
- Remaining steps are re-numbered sequentially
- If no steps remain → returns None → falls back to direct execution

### 1d. Planner Revision Retry

**Modified**: `src/planning/planner.py`

- `revise()` now retries once on parse failure, same pattern as `plan()`:
  - Adds the failed response to context
  - Sends correction message: "Your response was not valid JSON..."
  - Calls provider again
  - If second attempt also fails → returns None → triggers critic fallback (1c)

## Safety Flow (After Phase 1)

```
Tool call (bash_exec "brew install X")
  → Guard: _PACKAGE_MANAGERS match → ESCALATE
  → Agent: creates Escalation, stops spinner
  → CLIUserGate: prints to stdout, reads y/n
  → User: "n"
  → Agent: returns "Tool call denied by user" to model
  → Model: acknowledges failure
```

```
Monitor: ESCALATE (step result suspicious)
  → Agent: creates Escalation
  → CLIUserGate: prompts user
  → User: "y" → step marked complete
  → User: "n" → step skipped, error logged
```
