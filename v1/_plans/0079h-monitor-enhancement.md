# 0079h — Phase H: Monitor Enhancement (Plan-Level Goal Awareness)

> **Read first:** `_plans/0079-runtime-as-god.md` §0.
> **Depends on:** 0079g (skills declare CompletionCriteria — the
> monitor reads them).

## Goal

Today the `ExecutionMonitor` (`src/runtime/monitor.py`) only assesses
**single-step success**. It cannot say "the goal is already met; stop
running remaining steps." That short-circuit belongs to
ContinuationStage at the plan boundary, but the monitor — which
already runs after every step — is the natural place to surface
**early goal achievement** so we don't waste tool calls running
unnecessary steps.

This addresses **DRIFT-6**.

## What we're adding

1. A new value on `StepDecision`: `GOAL_ACHIEVED`.
2. ExecutionStage handling for `GOAL_ACHIEVED` — short-circuits the
   remaining queue, marks remaining steps as skipped, exits the
   per-step loop cleanly.
3. Monitor logic — when the active skill has a `CompletionCriteria`
   that evaluates `MET` on the just-finished step's result, the
   monitor returns `GOAL_ACHIEVED`. Without skill criteria, the
   monitor's behavior is unchanged.
4. The same `GOAL_ACHIEVED` signal is observable to ContinuationStage
   via the plan state (steps marked completed/skipped) — no new
   PipelineContext field needed.

## Files

| File | Why |
|------|-----|
| `src/runtime/schema.py` | Add `GOAL_ACHIEVED` to `StepDecision`. |
| `src/runtime/monitor.py` | Add per-step criteria check; emit `GOAL_ACHIEVED` when skill criteria are MET. |
| `src/runtime/stages/execution.py` | Handle `GOAL_ACHIEVED` in the decision dispatch (lines 309-405). |
| `src/runtime/prompts.py` | Update `MONITOR_USER_TEMPLATE` doc-string in case any reference; the LLM monitor itself does not need to know about `GOAL_ACHIEVED` — the new path is structural and sits before the LLM call. |

## Detailed changes

### Change 1 — Schema

**File:** `src/runtime/schema.py:17-23`

```python
class StepDecision(str, Enum):
    CONTINUE      = "continue"
    RETRY         = "retry"
    REPLAN        = "replan"
    DEFER         = "defer"
    SKIP          = "skip"
    ESCALATE      = "escalate"
    GOAL_ACHIEVED = "goal_achieved"     # ← NEW
```

### Change 2 — Monitor: pre-LLM criteria check

**File:** `src/runtime/monitor.py`

The monitor today:
1. Heuristic triage (lines 51-57)
2. If flagged, LLM assess (line 70)

Insert a **structural goal check** between heuristic triage and
LLM assessment, but only on the path where heuristics PASS — meaning
the step succeeded and we should ask "are we already done?"

```python
# src/runtime/monitor.py
def __init__(self, provider: BaseProvider, skill_registry=None):
    self._provider = provider
    self._skill_registry = skill_registry  # injected from agent.py

def assess(
    self, step: Step, plan: Plan, result: str,
    *, active_skill_name: str | None = None,
) -> StepAssessment:
    if not config.runtime.execution_monitor.enabled:
        return StepAssessment(decision=StepDecision.CONTINUE, reason="monitor disabled")

    flags = self._heuristic_triage(step, result)

    if not flags:
        # Heuristics PASS — step succeeded. Before declaring CONTINUE,
        # ask whether the active skill's criteria are now met.
        if active_skill_name and self._skill_registry is not None:
            outcome = self._check_skill_criteria(active_skill_name, step, plan, result)
            if outcome:
                logger.info(f"  monitor: skill '{active_skill_name}' criteria MET → GOAL_ACHIEVED")
                return StepAssessment(
                    decision=StepDecision.GOAL_ACHIEVED,
                    reason=f"skill '{active_skill_name}' completion criteria satisfied",
                    confidence=1.0,
                )
        logger.info("  monitor: heuristics PASS → auto-CONTINUE")
        return StepAssessment(decision=StepDecision.CONTINUE, reason="heuristics pass")

    # ... rest of method unchanged ...
```

`_check_skill_criteria`:

```python
def _check_skill_criteria(
    self, skill_name: str, step: Step, plan: Plan, result: str,
) -> bool:
    """Return True iff the active skill's structural criteria are MET
    after this step. LLM-judged criteria are NOT evaluated here — they
    belong to ContinuationStage where the LLM call cost is paid once
    per plan, not once per step.
    """
    skill = self._skill_registry.get(skill_name)
    if skill is None:
        return False
    criteria = skill.completion_criteria
    if criteria is None:
        return False
    from skills.criteria import StructuralCriteria, CriteriaContext, CriteriaOutcome
    if not isinstance(criteria, StructuralCriteria):
        return False
    # Ensure the step we just finished produced the right tool output;
    # the structural criterion looks at the most recent matching step.
    cctx = CriteriaContext(plan=plan, user_message=plan.original_query)
    try:
        return criteria.evaluate(cctx) == CriteriaOutcome.MET
    except Exception as e:
        logger.info(f"  monitor: criteria eval raised ({e!r}) — ignoring")
        return False
```

This keeps the per-step monitor cheap: only structural criteria run
here, and they're typically a JSON parse on the step's result string.

### Change 3 — ExecutionStage handling of `GOAL_ACHIEVED`

**File:** `src/runtime/stages/execution.py`

In the dispatch block at lines 309-405, after the `SKIP` branch and
before `ESCALATE`, add:

```python
elif decision == StepDecision.GOAL_ACHIEVED:
    step.status = StepStatus.COMPLETED
    logger.info(banner(f"Goal achieved at step {step.step}/{n_total}"))
    if _identity is not None:
        _bus.emit(RuntimeEvent(
            "goal.achieved",
            _identity,
            payload={"at_step": step.step, "remaining": n_total - step.step},
            stage="ExecutionStage",
        ))
    # Mark all remaining queued steps as skipped so plan summary is honest.
    for remaining in queue[idx + 1:]:
        remaining.status = StepStatus.COMPLETED
        remaining.flags.skipped = True
        remaining.result = "(skipped — goal achieved earlier)"
    # Exit the loop.
    idx = len(queue)
    continue
```

The `continue` after `idx = len(queue)` lets the `while idx < len(queue):`
condition exit cleanly on the next iteration.

### Change 4 — Pass `active_skill_name` to the monitor

**File:** `src/runtime/stages/execution.py`

The `_execute_plan` method calls `self._monitor.assess(step, plan, result)`
at line 306. Update the call site to pass the active skill name:

```python
assessment = self._monitor.assess(
    step, plan, result or "",
    active_skill_name=getattr(self, "_active_skill_name", None),
)
```

Set `self._active_skill_name` at the start of `run`:

```python
def run(self, context: PipelineContext) -> StageResult:
    if context.plan is None:
        return StageResult(status=StageStatus.OK, updated_context=context)
    self._active_skill_name = context.active_skill_name
    # ... rest unchanged ...
```

### Change 5 — Inject SkillRegistry into ExecutionMonitor

**File:** `src/agent.py`

In `Agent.__init__` (around line 137):

```python
self.monitor = ExecutionMonitor(
    get_runtime_provider(),
    skill_registry=self.skill_registry,
)
```

If `ExecutionMonitor.__init__` was a positional-only call elsewhere
(grep `ExecutionMonitor(`), update those sites too. Likely only one
call site (`agent.py`).

### Change 6 — ContinuationStage interaction

When ExecutionStage sets remaining steps to `skipped` and exits early,
ContinuationStage receives a plan where:
- Some steps `COMPLETED` with results
- Some steps `COMPLETED` with `flags.skipped=True` and a sentinel result

Its decision logic already handles "fewer steps with content than
queued" (per 0079d §11). The structural skill-criteria evaluator will
again return `MET` (since the criterion already triggered the early
exit), so ContinuationStage decides `SYNTHESIZE`. No additional code
changes needed in ContinuationStage; just confirm via a smoke test.

## Verification

```bash
pytest -x -q

# 1. Run a test-reconstruction flow where the candidate is already
#    correct. After step 2 (diff_behavior) returns all_match=true,
#    the monitor should log:
#      "monitor: skill 'test-reconstruction' criteria MET → GOAL_ACHIEVED"
#    ExecutionStage should log:
#      "Goal achieved at step 2/3"
#    Step 3 should not run; ContinuationStage should decide SYNTHESIZE.

# 2. Run a flow where criteria are NOT MET — monitor's behavior should
#    be unchanged. Look for normal "heuristics PASS → auto-CONTINUE".

# 3. Run a query that doesn't go through a skill (planner-only plan).
#    active_skill_name is None; the new code path is bypassed; behavior
#    is identical to pre-phase.
```

## Done when

- [ ] `StepDecision.GOAL_ACHIEVED` exists.
- [ ] `ExecutionMonitor` consults skill structural criteria when
      heuristics PASS and returns `GOAL_ACHIEVED` when MET.
- [ ] `ExecutionStage` handles `GOAL_ACHIEVED` by marking remaining
      steps skipped and exiting the loop.
- [ ] `pytest` green.
- [ ] Smoke test of test-reconstruction with already-passing candidate
      shows the early exit.

## Out of scope

- LLM-judged criteria at the per-step level. Reserved for
  ContinuationStage where the cost is amortized.
- Reverse direction: `GOAL_VIOLATED` (the monitor declaring the goal
  has been broken). Not in the brief; consider in a future iteration.
- Council-driven goal assessment. The monitor council
  (`config.runtime.monitor_council`) remains as-is for low-confidence
  decisions; it does not yet vote on `GOAL_ACHIEVED` since the
  structural check is high-confidence by construction.
