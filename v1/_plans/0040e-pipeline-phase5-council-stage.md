# 0040e — Pipeline Phase 5: CouncilStage

## What Was Implemented

Implemented `CouncilStage` in `src/runtime/stages/council.py`.
Also extracted `_strip_challenged_steps` from `agent.py` as a module-level
function in the same file. `agent.py` is still untouched.

## Files Created

### `src/runtime/stages/council.py`

**`_strip_challenged_steps(plan, critic_result) -> Plan | None`** (module-level)

Extracted from `Agent._strip_challenged_steps`. Removes steps the council
challenged with `drop` or `replace` suggestions. `justify` challenges are
kept (benefit of the doubt). Re-numbers remaining steps. Returns `None` if
all steps were stripped.

**`CouncilStage`**

Adversarial plan critic review. Key behaviors:

- **Workflow bypass**: plans routed via `classifier_hint`,
  `classifier_hint_direct`, `regex`, or `fallback` skip the council entirely.
  Pre-designed workflow plans are trusted — the critic is for hallucinated
  planner output.
- **Dynamic scaling**: reads `config.runtime.council.dynamic_scaling` to
  select N councillors based on risk level. `low=0` skips; `moderate=1`;
  `high=N` uses full pool.
- **JUSTIFY-only shortcut**: if all challenges are `justify`, skip the
  expensive revision call — the plan is structurally sound.
- **Revision path**: sends challenges to `planner.revise()`, validates the
  result. Falls back to stripping if revision fails or is invalid.
- **ABORT on empty plan**: if stripping removes all steps, returns `ABORT`
  so the pipeline falls back to `DirectExecutionStage`. This prevents the
  old broken behavior of executing a 1-step plan with nothing to synthesize.

Dependencies injected: `PlanCritic`, `Planner`, `PlanValidator`, `spinner`.

## Next Phase

Phase 6 — implement `ExecutionStage` and `SynthesizerStage`.
