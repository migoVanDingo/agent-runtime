# 0040h — Pipeline Phase 8: Stage Gate Hardening

## What Was Implemented

Added enforcement gates to three stages: `EntityCriticStage`, `ValidatorStage`,
and `CouncilStage`. These gates prevent the classes of silent failure that
were identified in prior sessions.

## Files Modified

### `src/runtime/stages/entity_critic.py`

**Problem:** The entity critic was silently replacing `/tmp/arc_disasm_proc.asm`
with `encryption/decryption` (a phrase extracted from tool output). The
candidate `encryption/decryption` has one slash but was actually two words
— not a valid file path.

**Fix:** After `EntityCritic.correct()` runs, each correction is checked with
`_is_suspicious_candidate(old, new)`:

- Suspicious if `new` has no slash (bare word, not a path)
- Suspicious if `new` is shorter than 3 characters

Suspicious corrections are **reverted** (the description mutation is undone
before the plan moves forward). If any suspicious corrections are found,
`ASK_USER` is returned with a confirmation question listing the uncertain
corrections. The pipeline runner will show this to the user, take their
response, and retry the stage.

Clean (non-suspicious) corrections are logged and applied as before.

This eliminates the silent corruption class: the critic can no longer replace
a legitimate path with a bare word from conversation context without the user
confirming it first.

### `src/runtime/stages/validator.py`

**Problem:** When ValidatorStage ABORTed due to a missing plan, the reason
was just "No plan available after planning stage" — no indication of *why*
the planner gave up.

**Fix:** The ABORT reason now appends `context.failure_reason` if set. Since
`PlanningStage` writes the last validation feedback into `failure_reason`
before ABORTing, the session log now shows e.g.:

```
pipeline: ABORT from 'PlanningStage' — max retries exceeded:
  Plan has 0 steps.
```

### `src/runtime/stages/council.py`

**Problem (already fixed in Phase 5):** All steps stripped → ABORT. ✓

**New Phase 8 addition:** A secondary coherence check catches the case where
stripping leaves only `CONVERSATION`-type steps but `requires_synthesis` is
True. A synthesis-only plan (no data-gathering steps) will always produce an
empty synthesis — the synthesizer has no step results to work with.

Check: after stripping, if `plan.requires_synthesis` and no non-CONVERSATION
steps remain → ABORT with reason "Plan stripped to synthesis-only: no
data-gathering steps remain". Pipeline falls back to `DirectExecutionStage`.

## Gate Summary

| Stage | Trigger | Response |
|-------|---------|----------|
| EntityCriticStage | Correction candidate has no slash or is < 3 chars | Revert + ASK_USER |
| ValidatorStage | plan is None | ABORT with failure_reason included |
| CouncilStage | All steps stripped | ABORT |
| CouncilStage | requires_synthesis but only CONVERSATION steps remain | ABORT |

## Implementation Complete

All 8 phases are now implemented. The pipeline is live. `agent.py` is 145 lines
with an 8-line `call()`. All stage logic lives in `src/runtime/stages/`.
