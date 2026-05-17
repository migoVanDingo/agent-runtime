# 0027 — Runtime Infrastructure Phase 5: Execution Monitor + Planner.replan()

## What

Closed-loop execution control. After each step completes, the monitor
assesses the result and decides: continue, retry, replan, defer, skip, or
escalate. This is the core of Cruz's "execution-time intervention"
principle.

## Execution Monitor

### Two-tier assessment

**Tier 1 — Heuristic triage (code, no LLM):**
- Empty or whitespace-only result → FLAG
- Error indicator patterns in result (regex: "error", "failed",
  "exception", "permission denied", "not found", "cannot", etc.) → FLAG
- Step error field is set → FLAG
- If no flags → auto-CONTINUE, zero cost

**Tier 2 — LLM assessment (only when flagged):**
- Uses runtime provider (gpt-4o-mini or equivalent)
- Ephemeral Messenger (same pattern as planner/synthesizer)
- Receives: original query, step description + result, completed steps
  summary, remaining steps, specific flag descriptions
- Returns JSON: decision + reason + optional suggestion

### Decisions

| Decision | Behavior |
|----------|----------|
| CONTINUE | Mark step complete, proceed |
| RETRY | Re-run step with failure context. Max `max_step_retries` (default 2). After max, auto-continue. |
| REPLAN | Call `Planner.replan()`. Replace remaining queue. If replan fails, continue. |
| DEFER | Move step to end of queue. Max 1 defer per step to prevent infinite loops. |
| SKIP | Mark step as skipped, proceed. For redundant steps. |
| ESCALATE | (Future) For now, treated as CONTINUE with a log warning. |

## Planner.replan()

New method on existing `Planner` class. Separate from `plan()`.

**Input:** the full Plan object, the failed Step, and the failure reason.

**Prompt includes:**
- Original user query
- Completed steps with result summaries
- The failed step and why it failed
- The now-invalidated remaining steps (for context)
- Step numbering starts at the failed step's number
- Step budget: `max_steps - completed_count`

**Output:** list of new `Step` objects, or None on failure.

## Changes

### New files

- **`src/runtime/monitor.py`** — `ExecutionMonitor` class:
  - `assess(step, plan, result) -> StepAssessment`
  - `_heuristic_triage(step, result) -> list[str]` (flag descriptions)
  - `_llm_assess(step, plan, result, flags) -> StepAssessment`
  - `_parse(raw) -> StepAssessment` (defaults to CONTINUE on failure)

### Modified files

- **`src/planning/planner.py`**:
  - New import: `Step`, `StepStatus`
  - New method: `replan(plan, failed_step, reason) -> list[Step] | None`

- **`src/agent.py`**:
  - Imports: added `ExecutionMonitor`, `StepDecision`
  - `__init__`: creates `self.monitor` with runtime provider
  - `_execute_plan()`: completely reworked from a simple for-loop to a
    queue-based execution loop with monitor integration:

    **Queue-based execution:**
    The step list becomes a mutable queue. An index pointer advances
    through it. Monitor decisions can:
    - Leave the pointer (RETRY — re-execute same index)
    - Pop and re-append (DEFER — step moves to end)
    - Splice in new steps (REPLAN — replace from current index onward)
    - Advance normally (CONTINUE, SKIP, ESCALATE)

    **Retry tracking:** `step.flags.retry_count` increments on each retry.
    When it hits `max_step_retries`, the step auto-continues regardless.

    **Defer guard:** a step can only be deferred once (`step.flags.deferred`).
    Second defer attempt auto-continues.

    **Replan integration:** on REPLAN, `planner.replan()` produces new
    steps that replace everything from the current position onward. The
    queue and `plan.steps` are both updated. If replan fails, the step
    is marked complete and execution continues.

    **Log banners:** `── Monitor: Step N/M ──` appears after each step,
    showing the assessment result. Retry attempts show the count.

## What does not change

- `_run_step()` — unchanged (still the inner ReAct loop)
- `_run_loop()` — unchanged (direct execution path)
- Synthesizer — unchanged (sees final plan.steps regardless of mutations)
- Runtime prompts — monitor prompts were already defined in phase 3
