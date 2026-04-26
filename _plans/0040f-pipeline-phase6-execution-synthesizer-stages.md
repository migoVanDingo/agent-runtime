# 0040f — Pipeline Phase 6: ExecutionStage + SynthesizerStage

## What Was Implemented

Implemented `ExecutionStage` and `SynthesizerStage` in
`src/runtime/stages/execution.py` and `src/runtime/stages/synthesizer.py`.

`agent.py` is still untouched.

## Files Created

### `src/runtime/stages/execution.py`

**Module-level helpers** (extracted from `Agent` methods):

- `_step_system(plan, current_step, agent_system) -> str` — builds the
  per-step system prompt with plan progress markers (✓ / → / space). Takes
  `agent_system` as a parameter rather than reading from `self`.
- `_step_utility_tools(step) -> list[str]` — returns utility tool names to
  add alongside the step's declared tool (`make_directory` for `write_file`,
  `read_file` for `bash_exec`).

**`ExecutionStage`**

Direct lift of `Agent._execute_plan()` + `Agent._run_step()`. All runtime
safeguards preserved:

- Pre-execution guard checks at step level and tool call level
- Monitor assessments (CONTINUE / RETRY / REPLAN / DEFER / SKIP / ESCALATE)
- Loop detection (repeated identical tool call → force wrap-up)
- Per-step tool call cap → forced wrap-up message
- `max_tokens` patching (dangling `tool_use` blocks get empty `tool_result`)
- Error recovery tracking (`step_tool_errors`, `error_recovery_clears_step_error`)
- LLM importance scoring per step result

When `plan.requires_synthesis` is True, `_execute_plan` returns `""` and
does NOT set `context.response` — `SynthesizerStage` will overwrite it.

Dependencies injected: `provider`, `registry`, `router`, `context_mgr`,
`messenger`, `monitor`, `guard`, `user_gate`, `importance_scorer`, `planner`,
`spinner`, `agent_system` (the base system prompt string).

### `src/runtime/stages/synthesizer.py`

**`SynthesizerStage`**

No-op unless `context.plan.requires_synthesis is True`. Calls
`Synthesizer.synthesize(plan)`, sets `context.response`, stops spinner.

Dependencies injected: `synthesizer`, `spinner`.

## Synthesis Handoff

`ExecutionStage` and `SynthesizerStage` coordinate via `plan.requires_synthesis`:

- If False: `ExecutionStage` sets `context.response` to the last completed
  step's result. `SynthesizerStage` is a no-op.
- If True: `ExecutionStage` sets `context.response = ""` (placeholder).
  `SynthesizerStage` overwrites it with the synthesized text.

## Next Phase

Phase 7 — implement `DirectExecutionStage` and cut over `agent.py`.
