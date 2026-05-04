# 0073 — Phase I: Plan vs PlanRun split + cleanup pass

## Goal

Split the spec (Plan/Step) from execution state (PlanRun/StepRun).
Combined with magic-number consolidation, retry-budget documentation,
path policy escalation, and logger module cleanup.

## Scope

1. **Plan/PlanRun split**
   - `planning/schema.py` — remove StepStatus, result, error, runtime flags from Step.
   - New `runtime/run_state.py` — StepRun, PlanRun with execution state.
   - `ExecutionStage` operates on PlanRun; converts at entry.
   
2. **Magic numbers → config**
   - `runtime/pipeline.py`: MAX_RETRIES/ASK_USER constants → RuntimeConfig.pipeline.
   - `runtime/tool_loop.py`: default caps → ToolLoopConfig defaults from config.
   - `config.py` / `config.yml`: add `pipeline` section.

3. **Retry-budget doc** — `docs/retry-budget.md`.

4. **Path policy escalation** — `PathPolicyDecision.allowed=False` emits
   Escalation when `escalate_on_deny: true` in config.

5. **Logger split** — extract `LogFormatting` palette state class from
   `logger.py` to `runtime/log_formatting.py`.

## Files touched

`planning/schema.py`, `runtime/run_state.py` (new), `runtime/stages/execution.py`,
`runtime/pipeline.py`, `runtime/tool_loop.py`, `config.py`, `config.yml`,
`docs/retry-budget.md` (new), `runtime/policy/paths.py`, `logger.py`,
`runtime/log_formatting.py` (new).

## Exit criteria

- `planning.schema.Step` has no `status`, `result`, or `error` field.
- `runtime/run_state.PlanRun` carries all execution state.
- `docs/retry-budget.md` exists.
- Tests: StepRun/PlanRun construction and spec/run boundary.
