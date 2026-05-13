# 0079e — Phase E: ContinuationStage Implementation

> **Read first:** `_plans/0079d-continuation-arch.md` (the design).
> Implements that design as a working **pass-through evaluator**:
> ContinuationStage exists, is wired into the pipeline, and runs on
> every plan-mode request — but its decision logic is intentionally
> conservative (always SYNTHESIZE unless the plan is empty).
>
> The completion-criteria evaluator (phase 0079g) and the monitor
> integration (phase 0079h) are added in later phases. This phase
> establishes the scaffolding without introducing new failure modes.
>
> Phase 0079f (schema cleanup) **depends on this phase** — we need
> ContinuationStage in place before deleting `requires_synthesis`.

## Goal

Land the smallest version of ContinuationStage that:

1. Lives in `src/runtime/stages/continuation.py`.
2. Has the data structures, config, and pipeline-context fields needed
   for later phases.
3. Holds a reference to `ExecutionStage` for loop-back (even though
   loop-back is not yet exercised — the call path exists).
4. Evaluates with a default **always-SYNTHESIZE** policy so behavior
   is functionally identical to today (modulo one extra cheap LLM call,
   which is gated behind a config flag and disabled by default in this
   phase — see §6).
5. Passes through `Plan.requires_synthesis` for backwards compatibility
   *during this phase only*. Phase 0079f deletes the field; this phase
   reads it as a transitional shim and prefers the ContinuationStage
   decision when both are present.

## Files

**New files:**
- `src/runtime/stages/continuation.py`

**Modified files:**
- `src/runtime/pipeline_context.py` — add `continuation_state` field
- `src/runtime/schema.py` — add `ContinuationDecision` enum, `ContinuationState` dataclass
- `src/config.py` — add `ContinuationConfig`, wire into `RuntimeConfig`
- `config.yml` — add `runtime.continuation` block
- `src/agent.py` — instantiate ContinuationStage and insert in pipeline

## Detailed changes

### Change 1 — Schema additions

**File:** `src/runtime/schema.py`

Add at the end of the file:

```python
# ── Continuation Stage ────────────────────────────────────────────

class ContinuationDecision(str, Enum):
    SYNTHESIZE = "synthesize"
    DONE       = "done"
    LOOP       = "loop"


@dataclass
class ContinuationState:
    iteration_count: int = 0
    last_decision: str | None = None
    artifacts_carried: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
```

You will need to import `field` from `dataclasses` if not already
imported.

### Change 2 — `PipelineContext` field

**File:** `src/runtime/pipeline_context.py`

Add the import:
```python
if TYPE_CHECKING:
    from runtime.schema import ClassifierResult, ContinuationState
```

Add the field (near the other runner-control fields, around line 58):

```python
# ── Continuation state (managed by ContinuationStage) ────────────
continuation_state: "ContinuationState | None" = None
```

We use `None` default + lazy init in the stage (rather than
`field(default_factory=...)`) to avoid creating state for direct-mode
or plan-less paths.

### Change 3 — `ContinuationConfig`

**File:** `src/config.py`

After `SynthesisQualityConfig` (around line 192):

```python
@dataclass
class ContinuationConfig:
    """Owns task-level completion decisions and continuation loops.

    See _plans/0079d-continuation-arch.md for the design.
    """
    enabled: bool = True
    max_iterations: int = 5
    # When False (this phase's default), the LLM judge is skipped and the
    # stage always returns SYNTHESIZE for non-empty plans. Allows the
    # stage to be wired into the pipeline before we trust its decisions.
    use_llm_judge: bool = False
    # When True, single-skill plans use ONLY the skill's CompletionCriteria
    # (no LLM judge). Phase 0079g adds the criteria; harmless flag here.
    trust_skill_criteria: bool = False
    llm_judge_label: str = "ContinuationStage"
```

Add to `RuntimeConfig`:

```python
continuation: ContinuationConfig = field(default_factory=ContinuationConfig)
```

Update the loader (around `src/config.py:370-456`) to read
`raw["runtime"].get("continuation", {})` and pass into `RuntimeConfig`.

### Change 4 — config.yml entry

**File:** `config.yml`

Under `runtime:`:

```yaml
runtime:
  # ... existing ...

  continuation:
    enabled: true
    max_iterations: 5
    use_llm_judge: false       # phase 0079e: pass-through; phase 0079h enables
    trust_skill_criteria: false
    llm_judge_label: "ContinuationStage"
```

### Change 5 — The ContinuationStage class

**New file:** `src/runtime/stages/continuation.py`

```python
"""ContinuationStage — owns task-level completion decisions.

This stage replaces Plan.requires_synthesis as the authority over what
happens after ExecutionStage finishes. It can also loop back to
ExecutionStage with a continuation plan.

This phase (0079e) ships a pass-through evaluator. The full decision
logic and skill-criteria evaluator arrive in phases 0079g/0079h.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from planning.planner import Planner
from planning.schema import Plan
from providers.base import BaseProvider, TextBlock
from runtime.pipeline_context import PipelineContext
from runtime.schema import ContinuationDecision, ContinuationState
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from app_config import config
from logger import get_logger

if TYPE_CHECKING:
    from runtime.stages.execution import ExecutionStage

logger = get_logger(__name__)


class ContinuationStage(Stage):
    """Owns the question 'are we done with the task?'.

    Reads:  context.plan, context.user_message, context.continuation_state
    Writes: context.continuation_state, possibly context.plan (on LOOP)

    Returns:
      OK    when synthesis should run next (default for non-empty plans this phase)
      DONE  when the answer is already in context.response and synthesis isn't needed
    """

    name = "ContinuationStage"

    def __init__(
        self,
        provider: BaseProvider,
        planner: Planner,
        execution_stage: "ExecutionStage",
        spinner,
    ) -> None:
        self._provider = provider
        self._planner = planner
        self._execution = execution_stage
        self._spinner = spinner

    def run(self, context: PipelineContext) -> StageResult:
        cfg = config.runtime.continuation

        # No-op when feature disabled or not in plan mode.
        if not cfg.enabled:
            return self._fall_through_legacy(context)
        if context.plan is None:
            return StageResult(status=StageStatus.OK, updated_context=context)

        # Lazy-init continuation state.
        if context.continuation_state is None:
            context.continuation_state = ContinuationState()

        logger.info(banner("Continuation"))

        # Loop bound. ContinuationStage may re-enter ExecutionStage internally;
        # the loop count is bounded by max_iterations.
        while True:
            decision = self._decide(context, cfg)
            context.continuation_state.last_decision = decision.value
            context.continuation_state.history.append({
                "iteration": context.continuation_state.iteration_count,
                "plan_steps": len(context.plan.steps) if context.plan else 0,
                "decision": decision.value,
            })

            if decision == ContinuationDecision.DONE:
                logger.info("  continuation: DONE — no synthesis needed")
                return StageResult(status=StageStatus.DONE, updated_context=context)

            if decision == ContinuationDecision.SYNTHESIZE:
                logger.info("  continuation: SYNTHESIZE — pass to synthesizer")
                return StageResult(status=StageStatus.OK, updated_context=context)

            # LOOP — see §6/§7 of design doc 0079d.
            new_plan = self._build_continuation_plan(context)
            if new_plan is None:
                logger.info("  continuation: LOOP requested but no continuation plan available — synthesizing instead")
                return StageResult(status=StageStatus.OK, updated_context=context)

            context.continuation_state.iteration_count += 1
            if context.continuation_state.iteration_count > cfg.max_iterations:
                logger.info(
                    f"  continuation: iteration cap ({cfg.max_iterations}) reached — synthesizing"
                )
                return StageResult(status=StageStatus.OK, updated_context=context)

            context.plan = new_plan
            logger.info(
                f"  continuation: LOOP iteration {context.continuation_state.iteration_count} — "
                f"{len(new_plan.steps)} new step(s)"
            )
            self._spinner.update(f"Continuation #{context.continuation_state.iteration_count}...")

            # Re-enter ExecutionStage with the new plan.
            self._execution.run(context)
            # Loop top — re-decide based on the new state.

    # ── Decision logic ──────────────────────────────────────────────

    def _decide(self, context: PipelineContext, cfg) -> ContinuationDecision:
        """Phase 0079e: conservative pass-through.

        Phase 0079g extends this with skill CompletionCriteria.
        Phase 0079h enables the LLM judge.
        """
        plan = context.plan
        if plan is None or not plan.steps:
            return ContinuationDecision.DONE

        # Transitional shim: respect the legacy field for one phase.
        # Phase 0079f deletes Plan.requires_synthesis; remove this branch then.
        legacy = getattr(plan, "requires_synthesis", True)
        if not legacy:
            return ContinuationDecision.DONE

        if cfg.use_llm_judge:
            return self._llm_judge(context, cfg)

        return ContinuationDecision.SYNTHESIZE

    def _llm_judge(self, context: PipelineContext, cfg) -> ContinuationDecision:
        """Single focused LLM call. Defaults to SYNTHESIZE on parse failure."""
        from messenger import Messenger
        from runtime.json_extract import extract_json

        plan = context.plan
        prior_lines = ""
        hist = context.continuation_state.history
        if hist:
            prior_lines = "\nPrior iterations:\n" + "\n".join(
                f"  iter {h['iteration']}: {h['decision']} ({h['plan_steps']} steps)"
                for h in hist[-3:]
            )

        system = (
            "You evaluate whether an autonomous agent has finished the user's task.\n"
            "Respond with strict JSON:\n"
            "{\"judgment\": \"done\"|\"need_more\"|\"trivial\", "
            "\"reason\": \"...\", \"missing\": \"...\"}\n\n"
            "done    — the executed plan addresses the request; synthesis recommended\n"
            "need_more — clear unmet requirement; describe in 'missing'\n"
            "trivial — single-tool answer that needs no synthesis\n"
        )
        user = (
            f"Original request: {context.user_message}\n\n"
            f"Executed plan ({len(plan.steps)} steps):\n{plan.summary()}\n\n"
            f"Iteration {context.continuation_state.iteration_count} "
            f"of max {cfg.max_iterations}.{prior_lines}"
        )

        messenger = Messenger()
        messenger.add_user_message(user)
        try:
            response = self._provider.chat(
                messages=messenger.get_messages(),
                tools=[],
                system=system,
                label=cfg.llm_judge_label,
            )
        except Exception as e:
            logger.info(f"  continuation: LLM judge call failed ({e!r}) — defaulting to SYNTHESIZE")
            return ContinuationDecision.SYNTHESIZE

        raw = next((b.text for b in response.content if isinstance(b, TextBlock)), "")
        data = extract_json(raw)
        if not isinstance(data, dict):
            logger.info("  continuation: LLM judge returned unparseable JSON — defaulting to SYNTHESIZE")
            return ContinuationDecision.SYNTHESIZE

        judgment = (data.get("judgment") or "").lower()
        reason = data.get("reason", "")
        missing = data.get("missing", "")
        logger.info(f"  continuation: judge={judgment} reason={reason!r} missing={missing!r}")

        if judgment == "trivial":
            return ContinuationDecision.DONE
        if judgment == "need_more":
            return ContinuationDecision.LOOP
        return ContinuationDecision.SYNTHESIZE

    # ── Continuation plan generation ───────────────────────────────

    def _build_continuation_plan(self, context: PipelineContext) -> Plan | None:
        """Phase 0079e: planner-replan only.

        Phase 0079g adds skill-replay tier (Skill.continuation_steps).
        """
        plan = context.plan
        if plan is None or not plan.steps:
            return None
        last_step = plan.steps[-1]
        reason = "continuation requested by ContinuationStage"
        new_steps = self._planner.replan(plan, last_step, reason)
        if not new_steps:
            return None
        # Build a fresh Plan that carries the original query and the new steps.
        return Plan(
            original_query=plan.original_query,
            steps=new_steps,
            requires_synthesis=True,   # transitional; deleted in 0079f
            risk=getattr(plan, "risk", "low"),
        )

    # ── Legacy fall-through (only when the stage is disabled) ──────

    def _fall_through_legacy(self, context: PipelineContext) -> StageResult:
        """Behave like the old pipeline when ContinuationStage is disabled.

        Honors Plan.requires_synthesis so disabling the stage is safe.
        """
        plan = context.plan
        if plan is None:
            return StageResult(status=StageStatus.OK, updated_context=context)
        if not getattr(plan, "requires_synthesis", True):
            return StageResult(status=StageStatus.DONE, updated_context=context)
        return StageResult(status=StageStatus.OK, updated_context=context)
```

### Change 6 — Wire into pipeline

**File:** `src/agent.py`

In `_build_pipeline`, the relevant section (current lines 79-94 after
phase 0079c) becomes:

```python
execution = ExecutionStage(
    provider=p.provider,
    registry=p.registry,
    router=p.router,
    context_mgr=p.context_mgr,
    messenger=p.messenger,
    monitor=p.monitor,
    guard=p.guard,
    user_gate=p.user_gate,
    importance_scorer=p.importance_scorer,
    planner=p.planner,
    spinner=p.spinner,
    agent_system=system,
)

stages = [
    RoutingStage(...),
    DirectInlineStage(...),
    SkillHintStage(...),
    PlanningStage(...),
    SkillExpansionStage(registry=p.skill_registry),
    EntityCriticStage(...),
    ValidatorStage(),
    CouncilStage(...),
    execution,
    ContinuationStage(
        provider=get_runtime_provider(),    # cheap runtime LLM
        planner=p.planner,
        execution_stage=execution,
        spinner=p.spinner,
    ),
    SynthesizerStage(synthesizer=p.synthesizer, spinner=p.spinner),
    direct_execution,
]
```

We bind `execution` to a local variable so we can pass it into
ContinuationStage. The old code constructed ExecutionStage inline; this
change keeps the rest unchanged.

### Change 7 — Update ExecutionStage to NOT short-circuit

**File:** `src/runtime/stages/execution.py:138-144`

Currently:
```python
# Return DONE when synthesis is not needed — this short-circuits the
# pipeline so DirectExecutionStage (the final stage) is never reached
# and cannot overwrite the response with a blank tool-loop result.
# When requires_synthesis=True, return OK so SynthesizerStage runs next.
if not context.plan.requires_synthesis:
    return StageResult(status=StageStatus.DONE, updated_context=context)
return StageResult(status=StageStatus.OK, updated_context=context)
```

Replace with:
```python
# ContinuationStage owns the next-step decision.
# ExecutionStage always returns OK; the pipeline runner advances to
# ContinuationStage which then decides DONE/SYNTHESIZE/LOOP.
return StageResult(status=StageStatus.OK, updated_context=context)
```

Also remove the parallel branch at `src/runtime/stages/execution.py:409-413`:

```python
if plan.requires_synthesis:
    logger.info(banner("Done (synthesis pending)"))
    return ""
```

Replace with: just `logger.info(banner("Execution complete"))`. Always
return the assembled "last completed step result" string from
`_execute_plan` regardless of synthesis decision; ContinuationStage
will overwrite `context.response` if it loops.

This change only touches the *return semantics* of ExecutionStage.
Everything inside the per-step loop (monitor calls, replan, etc.)
remains exactly as-is.

## Verification

```bash
pytest -x -q

# Smoke: trivial plan-mode query
#   - ExecutionStage returns OK
#   - ContinuationStage logs "continuation: SYNTHESIZE" (since use_llm_judge=false)
#   - SynthesizerStage runs as before
python -m src.main <<< "list files in /tmp"

# Smoke: enable LLM judge via config and run again — judgments should
# log; behavior should not regress.
# (Edit config.yml to set runtime.continuation.use_llm_judge: true)

# Negative: plan with requires_synthesis=False (still set by the
# transitional skill code prior to phase 0079f) — ContinuationStage
# returns DONE; no synthesis runs. Same end-state as before.
```

## Done when

- [ ] `src/runtime/stages/continuation.py` exists and matches the
      design in 0079d.
- [ ] `runtime/schema.py` has `ContinuationDecision` and `ContinuationState`.
- [ ] `PipelineContext.continuation_state` field present.
- [ ] `RuntimeConfig.continuation` field present; `config.yml` updated.
- [ ] ExecutionStage no longer returns `DONE` based on
      `requires_synthesis`; it always returns `OK`.
- [ ] `agent.py` instantiates ContinuationStage and inserts it between
      ExecutionStage and SynthesizerStage.
- [ ] `pytest` green.
- [ ] End-to-end behavior is **identical** to pre-phase for any
      existing flow (we have not yet enabled the LLM judge, so the
      stage is a no-op for non-skill plans and a transitional shim for
      legacy `requires_synthesis=False` plans).

## Out of scope

- Skill `CompletionCriteria` evaluation — phase **0079g**.
- The `GOAL_ACHIEVED` monitor decision — phase **0079h**.
- Deletion of `Plan.requires_synthesis` — phase **0079f**.
- Skill replay tier in `_build_continuation_plan` — phase **0079g**.
