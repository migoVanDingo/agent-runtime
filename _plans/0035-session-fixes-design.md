---
# 0035 — Session Fixes: Entity Critic, Context Fidelity, Loop Cap

**Date**: 2026-04-18
**Status**: Planned
**Phases**: 0035a, 0035b, 0035c

## Background

Three bugs were observed in a live session running Grok-3-mini (main) + gpt-4o-mini (runtime):

1. The entity critic over-corrected a new output filename that the user explicitly stated in their message, replacing `_tests/grok-proc-output.txt` with `_tests/grok-proc-analysis-deep.md` because the new filename wasn't in prior conversation history.

2. The context manager reduced tool_result messages from the active plan to PLACEHOLDER fidelity. The model then used placeholder text (`[tool result: 97 chars]`) as the content argument to write_file, writing garbage to the output file instead of the actual result value.

3. The direct execution loop (`_run_loop`) had no hard iteration cap. When grok_python.py was called with wrong arguments 9+ times, the loop ran 12 iterations, cycling through two error-correction-injection rounds, and ended not by hitting a cap but by silently hanging after the final context pack — no "Done" banner, no clean exit.

## Fix A — Entity Critic: Current Message as Authoritative Context

**File**: `src/runtime/entity_critic.py`, `src/agent.py`

**Root cause**: `EntityCritic.correct()` receives only prior conversation history as context. The current user message is not visible to it. When a user introduces a new filename in their message, the critic sees it in the plan (planner correctly used it) but not in history, and "corrects" it away.

**Fix**: Pass `user_message` as a second argument to `entity_critic.correct()`. Extract entities from `user_message` into an "authoritative" set. Never correct plan references that match entities in this set — they were explicitly stated by the user.

**Invariant**: Entities mentioned in the current user message are authoritative. Prior history provides candidates for resolving ambiguous references only.

## Fix B — Context Manager: Protect Active Plan Results from PLACEHOLDER

**File**: `src/runtime/context_manager.py`

**Root cause**: The plan-awareness boost (Phase 6) raises importance of current-plan messages to HIGH, but the score for HIGH still depends on semantic similarity. A base64-encoded string has low semantic similarity to "write decrypted output to file", so its score falls below the PLACEHOLDER threshold despite being the data the model needs to act on.

**Fix**: In `_assign_fidelity`, receive `plan_start_index`. Any message at index >= plan_start_index gets a minimum fidelity of COMPRESSED — never PLACEHOLDER. This guarantees that data produced during the current plan execution is always present in the context in at least summarized form, never as a token-count placeholder.

**Implication**: The token budget for current-plan messages must be compressed content, not replaced with stubs. If the budget is still exceeded, compress more aggressively — but never drop the content entirely.

## Fix C — Run Loop: Hard Iteration Cap

**File**: `src/agent.py`

**Root cause**: `_run_loop` tracks `total_tool_calls` and `consecutive_errors`, both of which can be reset mid-run. There is no cap on `iteration` itself. The loop can run indefinitely if the model keeps making tool calls without triggering the reset conditions simultaneously.

**Fix**: Add `_DIRECT_MAX_ITERATIONS = 20` class constant. Check at the top of the while loop: if `iteration > _DIRECT_MAX_ITERATIONS`, inject a wrap-up message, do one final provider.chat() call, and return. This is a hard backstop — not a soft hint.

## Phase Summary

| Phase | Fix | Files Changed |
|-------|-----|---------------|
| 0035a | Entity critic user_message authority | entity_critic.py, agent.py |
| 0035b | Context fidelity floor for active plan | context_manager.py |
| 0035c | Run loop hard iteration cap | agent.py |
---
