# 0040a — Pipeline Phase 1: Scaffolding

## What Was Implemented

Created the four core pipeline abstractions. No existing behavior changed.
Nothing is wired into `agent.py` yet.

## Files Created

### `src/runtime/pipeline_context.py`
`PipelineContext` dataclass — the shared state baton that flows through
every stage. Fields grouped by the stage that first populates them:
- `user_message` — set at call-site
- `packed_messages`, `classification`, `answer_text`, `entity_context` — set by RoutingStage
- `routing_path` — set by WorkflowMatchStage
- `plan` — set by WorkflowMatchStage or PlanningStage
- `response` — set by ExecutionStage or SynthesizerStage
- `retry_count`, `failure_reason` — managed by the pipeline runner

### `src/runtime/stage_result.py`
`StageStatus` enum and `StageResult` dataclass. Every stage returns a
`StageResult`. Status values:
- `OK` — advance to next stage
- `DONE` — return `context.response` immediately (short-circuit)
- `RETRY` — re-run this stage with `failure_reason` injected
- `ASK_USER` — pause, ask user a question, retry with their response appended
- `ABORT` — unrecoverable; runner jumps to `DirectExecutionStage` fallback

### `src/runtime/stage_base.py`
`Stage` ABC. One abstract method: `run(context) -> StageResult`.
One abstract property: `name` (used in log banners).
Contract documented in docstring: stages must not raise for recoverable
failures, must always return a result, must not write fields belonging to
later stages.

### `src/runtime/pipeline.py`
`Pipeline` runner. Holds an ordered list of stages and a fallback stage.
Transition logic:
- `OK` → advance index, reset `retry_count`/`failure_reason`
- `DONE` → return immediately
- `RETRY` → re-run same stage (max `_MAX_RETRIES_PER_STAGE = 2`)
- `ASK_USER` → call `user_input_fn(question)`, append response to
  `context.user_message`, retry (max `_MAX_ASK_USER_PER_STAGE = 1`)
- `ABORT` → run fallback stage; if fallback also ABORTs return `""`

All RETRY/ASK_USER loops are handled inside `_run_stage()` so the main
`run()` loop only sees OK, DONE, and ABORT.

## No Behavior Change

`agent.py` is untouched. The pipeline is importable but not wired to
anything. Existing call flow is identical.

## Next Phase

Phase 2 — extract shared helpers to `src/runtime/utils.py` and implement
`RoutingStage` + `DirectInlineStage`.
