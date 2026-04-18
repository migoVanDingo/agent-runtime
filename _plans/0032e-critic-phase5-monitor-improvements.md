# 0032e — Plan Critic Phase 5: Monitor Improvements

**Date**: 2026-04-15
**Status**: Implemented
**Parent**: 0032

## Problems Addressed

From the haiku log (SES01KP9PHF71Y20YK7ENJYHPQJGY):
1. readelf failed 3 times ("command not found") — monitor said "heuristics PASS"
2. checksec failed ("not found") — monitor said "heuristics PASS"
3. Step 7 hit max_tokens — monitor said "heuristics PASS"

Root cause: the monitor's `_heuristic_triage` checked `result` (the model's text response) and `step.error`, but tool errors were in the message history — invisible to the monitor. And `max_tokens` was logged but not flagged.

## Changes

### 1. `agent.py` — `_run_step` now tracks tool errors
- New `step_tool_errors` list accumulates tool-level errors during execution
- After each tool call, if `_has_error_indicator(result)` is true, the error is appended
- When the step finishes, tool errors are written to `step.error`
- `max_tokens` stop reason is also written to `step.error`

### 2. Monitor catches errors automatically
The monitor's `_heuristic_triage` already checks `step.error` (line 51-52). Since we now populate `step.error` with:
- `"max_tokens"` when the step was cut off
- `"tool errors: nm: command not found; checksec: not found"` when tools fail

...the monitor will flag these and invoke the LLM assessment, which can then decide RETRY, SKIP, or REPLAN instead of blindly continuing.

### Before (haiku log)
```
readelf → "command not found" (x3)
bash_exec → "Error: missing required field(s): command"
monitor: heuristics PASS → auto-CONTINUE  ← wrong!
```

### After
```
readelf → "command not found"
step.error = "tool errors: readelf: STDERR: command not found"
monitor: heuristics FLAGGED — ['step error field set: tool errors: readelf...']
monitor LLM: skip — readelf is not available on this system
```
