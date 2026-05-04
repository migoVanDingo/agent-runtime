# 0066 — Phase B: Identity through PipelineContext

## Goal

`RuntimeIdentity` currently lives in a module-level global mutated by
`set_runtime_identity()`. The Council uses `ThreadPoolExecutor`; thread
workers race on the global. Every event today has `pipeline_run_id: null`,
`plan_id: null`, `step_run_id: null` — no causal joins are possible in
the dataset. This phase moves identity into `PipelineContext` so it flows
explicitly through all stages without globals.

## Scope

- Add `identity: RuntimeIdentity` to `PipelineContext`.
- `Pipeline.run` mints `pipeline_run_id` on entry.
- `PlanningStage` mints `plan_id` when a plan is created.
- `ExecutionStage` mints `plan_run_id` at plan start and `step_run_id`
  per step; `for_tool_call()` per tool call.
- `Council.deliberate` accepts `identity` parameter so thread workers
  use the correct identity rather than the global.
- `get_runtime_identity()` remains as a session-scope fallback for
  code outside a pipeline run (main.py session events).
- All existing event-emit sites updated to prefer context-bound identity.

## Files touched

`runtime/pipeline_context.py`, `runtime/pipeline.py`,
`runtime/stages/planning.py`, `runtime/stages/execution.py`,
`runtime/stages/direct_execution.py`, `runtime/council.py`,
`runtime/tool_executor.py`, `runtime/stages/council.py`,
`runtime/stages/routing.py`, `runtime/stages/workflow_match.py`,
`runtime/stages/entity_critic.py`, `runtime/stages/synthesizer.py`,
`runtime/stages/validator.py`, `main.py`.

## Exit criteria

- Tool-call events in `_events/*.jsonl` have non-null `pipeline_run_id`
  and `tool_call_id`.
- Council deliberation in a thread pool uses per-call identity.
- `grep -rn "get_runtime_identity()" src/runtime/` only hits non-pipeline code.
- Tests: pipeline runner mints pipeline_run_id; council workers
  receive distinct per-call tool_call_ids sharing the same pipeline_run_id.
