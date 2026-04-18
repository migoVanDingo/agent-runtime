# 0035c — Fix: Run Loop Hard Iteration Cap

**Date**: 2026-04-18
**Status**: Implemented
**Parent**: 0035

## Problem

`_run_loop` had no hard cap on `iteration`. The existing soft limits
(`_DIRECT_MAX_TOOL_CALLS`, `_DIRECT_MAX_CONSECUTIVE_ERRORS`) inject
hint messages but both can be reset mid-run. In session
SES01KPGBW02SEP98G9ZDV5G9RDZV, the loop ran 12 iterations across two
error-injection cycles without stopping. It ended by silently hanging
after the final context pack — no "Done" banner, no clean exit, no
response to the user.

## Fix

Added `_DIRECT_MAX_ITERATIONS = 20` class constant. At the top of the
while loop, if `iteration > _DIRECT_MAX_ITERATIONS`:

1. Log the cap hit
2. Inject a wrap-up system message
3. Call `provider.chat()` with `tools=[]` — forces a text response, no more tool calls
4. Add the response to messenger, log Done, return

This is a hard backstop that guarantees the loop always terminates and
always produces a response. The user gets a summary of what succeeded and
what failed rather than a spinning cursor that never resolves.

## Changes

- `src/agent.py` — `_DIRECT_MAX_ITERATIONS = 20`, iteration check at top of loop
