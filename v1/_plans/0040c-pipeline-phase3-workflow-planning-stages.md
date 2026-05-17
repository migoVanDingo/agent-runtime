# 0040c — Pipeline Phase 3: WorkflowMatchStage + PlanningStage

## What Was Implemented

Implemented the two plan-path stages that together decide *how* a plan is
produced: `WorkflowMatchStage` tries to match a pre-built workflow, and
`PlanningStage` falls back to the full LLM planner when no workflow matched.

Both stages gate on `classification.mode == "plan"` and are no-ops for direct
mode. `agent.py` is still untouched.

## Files Created

### `src/runtime/stages/workflow_match.py`

**`WorkflowMatchStage`**

Tries three paths in order to find a pre-built workflow plan:

1. **Classifier hint** — uses `context.classification.workflow_hint` from
   `RoutingStage`. If the named workflow exists, runs `try_match()` first
   (pattern confirmation), then falls back to `generate_plan(None, ...)` if
   the pattern miss.
2. **Regex match** — calls `workflow_matcher.match(user_message)` against all
   registered workflows.
3. **Targeted fallback** — dedicated `WorkflowSelector.select()` LLM call when
   both above miss; on a hit, calls `generate_plan(None, ...)`.

A `None` plan after all three paths is not a failure — it signals that
`PlanningStage` should run the full planner.

Writes `context.plan` and `context.routing_path`. Always returns `OK`.

### `src/runtime/stages/planning.py`

**`PlanningStage`**

Runs the full LLM planner (`Planner.plan()`) when `context.plan is None`
after `WorkflowMatchStage`. Uses `context.packed_messages` (full compressed
history) instead of the old truncated 250-char string.

On retry, `context.failure_reason` is appended to `user_message` so the
planner sees validation feedback from the previous attempt.

Sets `plan.risk` from `context.classification.risk` before writing to context.

Returns `ABORT` if the planner returns `None` — the pipeline runner will invoke
`DirectExecutionStage` as the fallback so the user always gets a response.

## No Behavior Change

`agent.py` is untouched. Stage implementations exist in parallel and are not
wired into `call()` yet.

## Next Phase

Phase 4 — implement `EntityCriticStage` and `ValidatorStage`.
