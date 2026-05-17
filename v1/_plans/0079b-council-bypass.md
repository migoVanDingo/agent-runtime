# 0079b — Phase B: Council Bypass Refactor

> **Read first:** `_plans/0079-runtime-as-god.md` §0 and §3.
> Independent of phase 0079a. Can be landed in either order.

## Goal

Remove the hardcoded `_WORKFLOW_PATHS` set from `CouncilStage` and
replace it with an **explicit, principled criterion** the council
applies to every plan, regardless of how the plan was produced. The
council's behavior must depend on the *plan*, not on metadata about
who made the plan.

This addresses **DRIFT-5**.

## What's broken today

`src/runtime/stages/council.py:26-29` and `:96-99`:

```python
# src/runtime/stages/council.py:26-29
_WORKFLOW_PATHS = {"classifier_hint", "classifier_hint_direct", "regex", "fallback", "selector"}
```

```python
# src/runtime/stages/council.py:96-99
if context.routing_path in _WORKFLOW_PATHS:
    logger.info(banner("Plan critic"))
    logger.info(f"  critic: skipped (workflow-generated plan via '{context.routing_path}')")
    return StageResult(status=StageStatus.OK, updated_context=context)
```

Two structural problems:
1. **Hidden coupling.** CouncilStage's behavior changes based on
   `context.routing_path` set by `WorkflowMatchStage`. There is no way
   to read CouncilStage in isolation and predict when it runs.
2. **Brittle enumeration.** Every time someone adds a new routing path
   (the brief notes "selector" was added by hand), this set must be
   updated. The brief explicitly calls this out as an incident.

A workflow plan that happens to include a destructive step bypasses
critique purely because of how it arrived — not because it has been
evaluated.

## Target design

The council runs (or skips) based on **explicit policy** computed from
the plan itself plus risk:

```
def should_run_council(plan: Plan, risk: str) -> tuple[bool, str]:
    """Return (run, reason). Reason is logged in either case."""
    # 1. High risk always runs council, regardless of provenance.
    if risk == "high":
        return True, "high risk"

    # 2. Plans with a high-complexity score run council.
    score = _plan_complexity(plan)
    threshold = config.runtime.plan_critic.complexity_threshold
    if score >= threshold:
        return True, f"complexity {score} >= {threshold}"

    # 3. Plans containing potentially destructive action types run council.
    destructive_action_types = {ActionType.SHELL, ActionType.FILE_IO}
    if any(s.action_type in destructive_action_types for s in plan.steps):
        # ...for low/moderate risk only this is the criterion. High already handled.
        if risk == "moderate":
            return True, "moderate risk + destructive action types present"

    # 4. Otherwise skip.
    return False, f"low/moderate risk, no destructive types, complexity {score} < {threshold}"
```

**Provenance is no longer load-bearing.** A skill-expanded plan gets the
same scrutiny as a planner-generated plan if its risk + structure say so.

`_plan_complexity()` is a small heuristic — start with:

```python
def _plan_complexity(plan: Plan) -> int:
    score = 0
    score += len(plan.steps)                            # 1 per step
    for s in plan.steps:
        if s.action_type in (ActionType.SHELL, ActionType.FILE_IO):
            score += 2                                  # destructive types
        if s.tool == "bash_exec":
            score += 1                                  # bash specifically
    return score
```

Numbers are starting points; surface the threshold in config so the user
can tune without code change.

## Files to change

| File | Why |
|------|-----|
| `src/config.py` | Add `complexity_threshold` to `PlanCriticConfig`. |
| `config.yml` | Set the threshold (suggest `complexity_threshold: 8`). |
| `src/runtime/stages/council.py` | Replace `_WORKFLOW_PATHS` block with policy function. |

## Detailed changes

### Change 1 — Extend `PlanCriticConfig`

**File:** `src/config.py:172-176`

Current:
```python
@dataclass
class PlanCriticConfig:
    enabled: bool
    skip_low_risk: bool = False
    consensus_on_high_risk: bool = True
```

After:
```python
@dataclass
class PlanCriticConfig:
    enabled: bool
    skip_low_risk: bool = False
    consensus_on_high_risk: bool = True
    # Explicit policy (replaces _WORKFLOW_PATHS bypass in CouncilStage).
    complexity_threshold: int = 8
```

Update the loader (around `src/config.py:370-456`) to read the new
key from `raw["runtime"]["plan_critic"]["complexity_threshold"]` with
default `8`.

### Change 2 — config.yml

**File:** `config.yml`

Under the existing `plan_critic:` block, add:

```yaml
runtime:
  plan_critic:
    enabled: true
    # ... existing options ...
    complexity_threshold: 8
```

### Change 3 — Replace the bypass in `council.py`

**File:** `src/runtime/stages/council.py`

Delete the `_WORKFLOW_PATHS` set at lines 26-29 entirely.

Add a private helper at module top (replacing `_WORKFLOW_PATHS`):

```python
from planning.schema import ActionType

_DESTRUCTIVE_ACTION_TYPES = {ActionType.SHELL, ActionType.FILE_IO}


def _plan_complexity(plan: Plan) -> int:
    """Heuristic structural complexity score.

    Starting recipe — tune via config rather than reshaping unless we have
    real evidence of mis-ranking.
    """
    score = len(plan.steps)
    for s in plan.steps:
        if s.action_type in _DESTRUCTIVE_ACTION_TYPES:
            score += 2
        if s.tool == "bash_exec":
            score += 1
    return score


def _should_run_council(plan: Plan, risk: str, threshold: int) -> tuple[bool, str]:
    """Return (run, reason). Reason is always logged.

    Provenance (routing_path, workflow_name) is intentionally NOT consulted.
    Council scrutiny depends on the plan, not on who made it.
    """
    if risk == "high":
        return True, "high risk"

    score = _plan_complexity(plan)
    if score >= threshold:
        return True, f"complexity score {score} >= threshold {threshold}"

    if risk == "moderate" and any(
        s.action_type in _DESTRUCTIVE_ACTION_TYPES for s in plan.steps
    ):
        return True, "moderate risk + destructive action types present"

    return False, (
        f"skip: risk={risk}, complexity={score} < {threshold}, "
        f"no destructive-types-on-moderate match"
    )
```

Replace the bypass block at lines 96-99:

**Before:**
```python
# Workflow-generated plans bypass the critic.
if context.routing_path in _WORKFLOW_PATHS:
    logger.info(banner("Plan critic"))
    logger.info(f"  critic: skipped (workflow-generated plan via '{context.routing_path}')")
    return StageResult(status=StageStatus.OK, updated_context=context)
```

**After:**
```python
# Explicit policy: council scrutiny is based on the plan, not on its provenance.
threshold = config.runtime.plan_critic.complexity_threshold
should_run, reason = _should_run_council(
    context.plan, context.classification.risk, threshold
)
logger.info(banner("Plan critic"))
if not should_run:
    logger.info(f"  critic: skipped — {reason}")
    return StageResult(status=StageStatus.OK, updated_context=context)
logger.info(f"  critic: running — {reason}")
```

Note that `logger.info(banner("Plan critic"))` was emitted unconditionally
before; we keep that behavior (now in both branches) so log output is
consistent.

### Change 4 — Search for other readers of `_WORKFLOW_PATHS` or `routing_path`

**Confirm via grep before completing this phase:**

```bash
rg -n "_WORKFLOW_PATHS" src/    # should return 0 hits after change
rg -n "routing_path" src/       # should return only stage-internal sets,
                                # not anything that switches behavior on it
```

If `routing_path` has other behavioral readers, list them in this phase
doc as follow-ups but do NOT remove them here — that belongs to
phase 0079c (skills system).

## Verification

```bash
# 1. Tests still pass
pytest -x -q

# 2. Run a low-risk planner-driven query — council should now apply
#    complexity threshold rather than blindly run
#    Look for log: "critic: skipped — skip: risk=low, complexity=N < 8 ..."
#    or            "critic: running — complexity score N >= threshold 8"

# 3. Run a high-risk query (destructive shell + multiple steps)
#    Look for log: "critic: running — high risk"

# 4. Run a workflow-matched query (e.g., deep-disassembly) — council
#    should now decide based on plan structure, not provenance.
#    For a long-multi-step deep-disassembly plan, expect the complexity
#    branch to trigger.
```

## Done when

- [ ] `_WORKFLOW_PATHS` is gone (zero hits in repo).
- [ ] CouncilStage decision is a function of plan + risk only — no
      reads of `context.routing_path` or `context.workflow_name`.
- [ ] Threshold lives in config; default is `8`.
- [ ] Logs show explicit reason ("high risk" / "complexity N >= 8" /
      "moderate risk + destructive types" / "skip").
- [ ] `pytest` green.

## Out of scope

- Eliminating `routing_path` / `workflow_name` from `PipelineContext` —
  they may still exist as informational logging fields. Removal happens
  in phase 0079c when the workflow→skill rename retires the workflow
  routing concept entirely.
- Tuning the heuristic. Phase 0079b ships a starting recipe; further
  tuning is a follow-up if real-world traffic shows mis-ranking.
