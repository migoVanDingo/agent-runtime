# 0040d — Pipeline Phase 4: EntityCriticStage + ValidatorStage

## What Was Implemented

Implemented `EntityCriticStage` and `ValidatorStage`, plus updated
`PlanningStage` to fold validation and its retry loop into the planning step.

`agent.py` is still untouched.

## Files Created / Updated

### `src/runtime/stages/entity_critic.py` (new)

**`EntityCriticStage`**

Runs the entity critic pass to correct hallucinated file/path references
before validation. Operates on all plans — both workflow-generated and
planner-generated. Skipped silently if `context.entity_context` is None
(nothing to compare against).

Calls `EntityCritic.correct(plan, entity_context, user_message=...)` and logs
each correction. Always returns `OK`.

### `src/runtime/stages/validator.py` (new)

**`ValidatorStage`**

Logs all plan steps (the session banner visible before execution begins) and
provides a hard nil guard — `ABORT` if `context.plan is None`. In practice
`PlanningStage` guarantees a non-None plan on `OK`, so this guard should never
fire; it exists as a safety net.

Always returns `OK` on a valid plan.

### `src/runtime/stages/planning.py` (updated)

Validation is now folded into `PlanningStage` because the retry loop requires
re-running the planner. The pipeline's `RETRY` mechanism re-runs this stage,
which is the only way to feed validation feedback back into the planner.

Updated behavior:
1. Plan (appending `failure_reason` if set — this is the validation feedback
   from the previous attempt injected by the pipeline runner).
2. Run `PlanValidator.validate()`.
3. If `INVALID`: return `RETRY` with `reason = validation.feedback`. The
   pipeline will re-run `PlanningStage` up to `_MAX_RETRIES_PER_STAGE` times
   before escalating to `ABORT`.
4. If `VALID`: set `plan.risk`, write to context, return `OK`.

`PlanningStage` now takes `validator: PlanValidator` as a constructor argument.

## Stage Order (plan mode)

```
RoutingStage
DirectInlineStage
WorkflowMatchStage
PlanningStage          ← plans + validates (RETRY on invalid)
EntityCriticStage      ← corrects hallucinated paths
ValidatorStage         ← logs steps + nil guard
[CouncilStage]         ← Phase 5
[ExecutionStage]       ← Phase 6
[SynthesizerStage]     ← Phase 6
```

## Next Phase

Phase 5 — implement `CouncilStage`.
