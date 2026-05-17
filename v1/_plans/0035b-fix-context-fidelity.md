# 0035b — Fix: Context Fidelity Floor for Active Plan

**Date**: 2026-04-18
**Status**: Implemented
**Parent**: 0035

## Problem

During active plan execution, the context manager could reduce tool_result
messages from the current plan to PLACEHOLDER fidelity. When the model then
needed to use that result as an argument to a subsequent tool (e.g. writing
a decoded string to a file), it saw only `[tool result: 97 chars]` and wrote
that placeholder text to the file instead of the actual value.

Root cause: the plan-awareness boost raises importance to HIGH, but score
is still `sim * (0.4 + 0.4 * recency)`. A base64-encoded string has low
semantic similarity to "write decrypted output to file", so even with the
importance boost the score can fall below the PLACEHOLDER threshold.

Observed in session SES01KPGBW02SEP98G9ZDV5G9RDZV: the output file
`_tests/grok-proc-output.txt` contained `[wrote 50 chars to ...]` —
the tool result confirmation metadata — instead of the decrypted string.

## Fix

`_assign_fidelity()` now accepts `plan_start_index`. After assigning fidelity
by score thresholds, a second pass enforces: any message at index >=
plan_start_index that would be PLACEHOLDER is raised to COMPRESSED.

COMPRESSED means the content is present (possibly truncated/summarized) but
not replaced with a token-count stub. The model can still read and use the
actual data value.

## Changes

- `src/runtime/context_manager.py`
  - `pack()` passes `plan_start_index` to `_assign_fidelity()`
  - `_assign_fidelity()` accepts `plan_start_index`, enforces COMPRESSED floor
