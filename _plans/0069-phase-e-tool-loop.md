# 0069 — Phase E: Outer ToolLoop extraction

## Goal

`ExecutionStage._run_step` and `DirectExecutionStage._run_loop` contain
~350 lines of duplicated ReAct loop machinery. This phase collapses them
into `runtime/tool_loop.py` with both stages becoming thin wrappers.

## Scope

- New `runtime/tool_loop.py`:
  - `ToolLoopConfig`: max_iterations, max_tool_calls, max_consecutive_errors,
    tool_result_truncate_chars, authorized_tool_names.
  - `ToolLoop.run(system, tools, query, hooks)`: full loop with force_end,
    repeat detection, error correction, dangling tool patching, injection gate,
    authorization check.
  - All existing runtime safeguards preserved.
- `ExecutionStage._run_step` → delegates to `ToolLoop`.
- `DirectExecutionStage._run_loop` → delegates to `ToolLoop`.
- Both stages shrink to ≤200 lines each.

## Files touched

`runtime/tool_loop.py` (new), `runtime/stages/execution.py`,
`runtime/stages/direct_execution.py`.

## Exit criteria

- Both stage files ≤200 lines.
- `runtime/tool_loop.py` contains all duplicated loop logic.
- Tests: termination, authorization rejection, repeat detection,
  max-tokens dangling tool patching.
