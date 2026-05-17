# 0034c — Phase 3: Direct Mode Hardening

**Date**: 2026-04-17
**Status**: Implemented
**Parent**: 0034

## Problem

Plan mode has validator, critic, guard, monitor, and tool-per-step enforcement. Direct mode had almost none of that — just the error correction injection from 0031. The guard was already wired in Phase 1a (tool-call-level ESCALATE works in both modes). This phase adds the remaining protections.

## Changes

### 3a. Guard in Direct Mode

Already done in Phase 1a. The `_run_loop` tool execution block now runs `guard.check_tool_call()` before every tool call, with BLOCK returning an error string and ESCALATE prompting the user via `user_gate`.

### 3b. Loop Limits

**Modified**: `src/agent.py` — `_run_loop()`

Added two counters:
- `consecutive_errors`: increments on each tool call with error indicators, resets on success. When ≥ 3, injects: *"Multiple consecutive tool calls have failed. Stop retrying and report the issue to the user."*
- `total_tool_calls`: increments on every tool call. When ≥ 15, injects: *"You have made many tool calls. Wrap up and respond to the user."* Resets after injection to allow one more round if the model genuinely needs it.

These are heuristic guards — no LLM calls, keeping direct mode fast.

### 3c. Tool Result Truncation

**Modified**: `src/agent.py` — `_run_loop()`

Before appending a tool result, checks its length. If > 50,000 chars (configurable via class constant `_DIRECT_MAX_TOOL_RESULT_CHARS`), truncates to that limit and appends `"[truncated — output was N chars, showing first 50000]"`.

This prevents a single large tool output (e.g., `strings` on a large binary) from overwhelming the context window in direct mode, where there's no plan-level tool selection to prevent it.

## Constants

```python
_DIRECT_MAX_TOOL_RESULT_CHARS = 50000
_DIRECT_MAX_TOOL_CALLS = 15
_DIRECT_MAX_CONSECUTIVE_ERRORS = 3
```

## Direct Mode Safety (After Phase 3)

```
Direct mode iteration:
  1. Guard check (BLOCK/ESCALATE/ALLOW) — Phase 1a
  2. Tool execution with safe_execute() — existing
  3. Result truncation if > 50K chars — Phase 3c
  4. Consecutive error tracking → stop message at 3 — Phase 3b
  5. Total tool call tracking → wrap-up message at 15 — Phase 3b
  6. Error correction injection if model ends turn after errors — existing (0031)
```
