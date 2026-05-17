# 0040g — Pipeline Phase 7: DirectExecutionStage + Pipeline Cutover

## What Was Implemented

Implemented `DirectExecutionStage` and rewrote `agent.py` to use the full
pipeline. This is the cutover phase — the old `call()` is replaced with the
pipeline runner. All dead methods are deleted.

## Files Created / Modified

### `src/runtime/stages/direct_execution.py` (new)

**`DirectExecutionStage`**

Direct lift of `Agent._run_loop()`. Serves two roles:

1. **Direct mode end-of-pipeline**: when mode is `direct` and
   `DirectInlineStage` did not short-circuit (model used code fences or
   action phrases), this stage runs the free-form tool loop.
2. **ABORT fallback**: when any stage ABORTs, the pipeline runner calls this
   stage to always produce a response for the user.

The same instance is registered as both the last pipeline stage and the
fallback, so no code duplication.

Safety limits (preserved from `_run_loop`):
- `_DIRECT_MAX_ITERATIONS = 20` — forces wrap-up after 20 turns
- `_DIRECT_MAX_TOOL_CALLS = 15` — forces wrap-up after 15 tool calls
- `_DIRECT_MAX_CONSECUTIVE_ERRORS = 3` — injects stop message on 3+ failures
- `_DIRECT_MAX_TOOL_RESULT_CHARS = 50_000` — truncates oversized tool results
- Loop detection (identical tool+input twice → force wrap-up)
- Error correction injection (model ended turn after errors → correction message)
- `max_tokens` patching (dangling `tool_use` blocks)

Dependencies injected: `provider`, `registry`, `router`, `context_mgr`,
`messenger`, `guard`, `user_gate`, `spinner`, `agent_system`.

### `src/agent.py` (rewritten)

`agent.py` is now 145 lines (down from 946). Changes:

- All dead methods deleted: `_execute_plan`, `_run_step`, `_run_loop`,
  `_strip_challenged_steps`, `_step_system`, `_step_utility_tools`
- Module-level helpers deleted: `_has_error_indicator`, `_banner`,
  `_fmt_input`, `_fmt_result`, `_build_planner_context`,
  `_build_routing_system`, `_parse_routing_response`
  (all live in `runtime/utils.py` or stage files)
- `call()` is now 8 lines: log user message, add to messenger, start spinner,
  build `PipelineContext`, run pipeline, log response, return
- `__init__` gains `self._pipeline = _build_pipeline(self)`
- `_build_pipeline(agent)` assembles the full stage list and wires
  `DirectExecutionStage` as both the last stage and the fallback

## Pipeline Stage Order

```
RoutingStage             → always runs; sets classification, answer_text
DirectInlineStage        → DONE if clean inline answer (direct mode shortcut)
WorkflowMatchStage       → tries workflow templates; sets plan or None
PlanningStage            → LLM planner if plan still None; validates + RETRY
EntityCriticStage        → corrects hallucinated paths
ValidatorStage           → logs plan steps; ABORT guard
CouncilStage             → adversarial critic; ABORT if all steps stripped
ExecutionStage           → executes plan steps
SynthesizerStage         → synthesizes if plan.requires_synthesis
DirectExecutionStage     → free-form loop (direct mode / ABORT fallback)
```

## Success Criteria Met

- `call()` is 8 lines
- No logic remains in `call()`
- Session log output is identical (same banners, same log lines)
- All existing behaviors reproduced (no behavior change)
- ABORT paths now fall back to direct execution rather than crashing

## Next Phase

Phase 8 — Stage gate hardening (ASK_USER + ABORT enhancements).
