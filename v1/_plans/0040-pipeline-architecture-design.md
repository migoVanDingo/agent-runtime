# 0040 — Pipeline Architecture: Tom Cruise & The Genius Lunatics

## Problem

`agent.py`'s `call()` method is a ~250-line sequential script with all
routing, planning, correction, validation, review, and execution logic
interleaved inline. There is no enforced contract between stages — a
corrupted plan from the entity critic moves to validation unchanged, a
gutted plan from the council moves to execution unchanged, and a model
that promises work it can't do moves to the user unchanged.

The runtime infrastructure exists (monitor, guard, validator, critic,
entity critic) but operates reactively — catching failures after they
happen rather than enforcing contracts between stages.

## Design Philosophy: Tom Cruise and the Genius Lunatics

Each processing stage (routing, planning, entity correction, validation,
council review, execution, synthesis) is a "genius lunatic" — capable of
impressive work but also capable of producing nonsense. The pipeline
runner is "Tom Cruise" — it doesn't do the thinking, it keeps each genius
on the rails and prevents bad output from one stage poisoning the next.

Concretely: nothing moves from stage N to stage N+1 unless the pipeline
runner allows it. Each stage declares what it guarantees about its output.
The runner enforces those guarantees.

## Core Abstractions

### PipelineContext

A single dataclass that is the shared state flowing through all stages.
Replaces the scattered local variables in `call()` today
(`plan`, `routing_path`, `packed`, `classification`, `answer_text`, etc.).

```python
@dataclass
class PipelineContext:
    user_message: str
    packed_messages: list[dict]        # set by RoutingStage
    classification: ClassifierResult   # set by RoutingStage
    answer_text: str                   # set by RoutingStage
    entity_context: str | None         # set by RoutingStage
    routing_path: str | None           # set by WorkflowMatchStage
    plan: Plan | None                  # set by WorkflowMatchStage or PlanningStage
    response: str | None               # set by ExecutionStage or SynthesizerStage
    retry_count: int                   # managed by pipeline runner
    failure_reason: str | None         # injected by runner on retry
```

### StageResult

Every stage returns a `StageResult`:

```python
@dataclass
class StageResult:
    status: StageStatus       # OK | RETRY | ASK_USER | ABORT | DONE
    updated_context: PipelineContext
    user_message: str | None  # question to show user (ASK_USER only)
    reason: str | None        # for logging (RETRY / ABORT)
```

Status semantics:
- `OK` — advance to next stage
- `DONE` — pipeline complete, return `context.response` immediately
- `RETRY` — transient failure, re-run this stage with `failure_reason` injected
- `ASK_USER` — stage needs human input; runner shows `user_message`, injects response, retries
- `ABORT` — unrecoverable failure; runner jumps to `DirectExecutionStage` fallback

### Stage Base Class

```python
class Stage(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def run(self, context: PipelineContext) -> StageResult: ...
```

### Pipeline Runner

Holds an ordered list of stages and a fallback stage
(`DirectExecutionStage`). Runs stages in sequence. On each `StageResult`:
- `OK` → advance index
- `DONE` → return immediately
- `RETRY` → re-run same stage (max 2 retries per stage, then ABORT)
- `ASK_USER` → call `user_input_fn(question)`, append response to
  `context.user_message`, retry same stage (max 1 ask per stage, then ABORT)
- `ABORT` → run fallback stage, return its response

## Stage Inventory

### Ordered pipeline stages

| # | Stage | File | Replaces |
|---|-------|------|----------|
| 1 | `RoutingStage` | `stages/routing.py` | Lines 186–204 of `call()` |
| 2 | `DirectInlineStage` | `stages/routing.py` | Lines 358–366 of `call()` |
| 3 | `WorkflowMatchStage` | `stages/workflow_match.py` | Lines 209–262 of `call()` |
| 4 | `PlanningStage` | `stages/planning.py` | Lines 263–267 of `call()` |
| 5 | `EntityCriticStage` | `stages/entity_critic.py` | Lines 269–277 of `call()` |
| 6 | `ValidatorStage` | `stages/validator.py` | Lines 281–291 of `call()` |
| 7 | `CouncilStage` | `stages/council.py` | Lines 293–357 of `call()` |
| 8 | `ExecutionStage` | `stages/execution.py` | `_execute_plan()`, `_run_step()` |
| 9 | `SynthesizerStage` | `stages/synthesizer.py` | Lines 464–470 of `_execute_plan()` |
| 10 | `DirectExecutionStage` | `stages/direct_execution.py` | `_run_loop()` |

`DirectExecutionStage` also serves as the ABORT fallback for any stage.

### Stage gate contracts (what each stage validates on input and guarantees on output)

| Stage | Input gate | Output guarantee | Failure behavior |
|-------|------------|------------------|------------------|
| RoutingStage | none | `classification` never None; defaults to `direct/low` on parse error | Always OK |
| DirectInlineStage | `classification` not None | If DONE: `response` is clean conversational text | OK or DONE |
| WorkflowMatchStage | `classification.mode == "plan"` else no-op | `plan` is valid Plan or None (None = planner should run) | Always OK |
| PlanningStage | `plan is None` else no-op | On OK: `plan` non-None with `original_query` and `risk` set | ABORT if planner returns None |
| EntityCriticStage | `plan` not None else no-op | Corrected plan with no tool-result false positives | Always OK |
| ValidatorStage | `plan` not None | On OK: plan passes structural validation | RETRY once (replans internally), then ABORT |
| CouncilStage | `plan` not None; bypassed for workflow plans | On OK: plan survived critic or was revised/stripped | ABORT if all steps stripped |
| ExecutionStage | `plan` not None | `response` always set (may be empty string) | Always OK (internal retry/replan loop handles failures) |
| SynthesizerStage | `plan.requires_synthesis` else no-op | `response` is coherent synthesized text | Always OK |
| DirectExecutionStage | none (fallback — accepts any context state) | `response` always set | Always OK |

## New Files

```
src/runtime/pipeline_context.py     PipelineContext dataclass
src/runtime/stage_result.py         StageResult + StageStatus
src/runtime/stage_base.py           Stage ABC
src/runtime/pipeline.py             Pipeline runner
src/runtime/stages/__init__.py
src/runtime/stages/routing.py       RoutingStage + DirectInlineStage
src/runtime/stages/workflow_match.py
src/runtime/stages/planning.py
src/runtime/stages/entity_critic.py
src/runtime/stages/validator.py
src/runtime/stages/council.py
src/runtime/stages/execution.py
src/runtime/stages/synthesizer.py
src/runtime/stages/direct_execution.py
```

## Modified Files

```
src/agent.py    call() → 5 lines; _execute_plan/_run_step/_run_loop/
                _strip_challenged_steps/_step_system/_step_utility_tools deleted
                (logic moves to stage files)
```

Shared helpers (`_has_error_indicator`, `_fmt_input`, `_fmt_result`,
`_banner`, `_build_routing_system`, `_parse_routing_response`) move to
`src/runtime/utils.py` so stage files can import them without circular
deps.

## What Does NOT Change

- All existing runtime classes: `Planner`, `PlanValidator`, `PlanCritic`,
  `EntityCritic`, `ExecutionMonitor`, `ActionGuard`, `Synthesizer`,
  `ContextManager`, `WorkflowMatcher`, `WorkflowSelector` — untouched.
- Tool registry, messenger, providers, config — untouched.
- All workflow implementations — untouched.
- Existing prompts — untouched.
- Session log format — untouched (stage banners use same `_banner()` helper).
- Behavior — this is a refactor, not a feature change. The pipeline
  produces identical outputs to the current `call()` for all inputs that
  currently succeed. The only behavioral difference is ABORT paths now
  fall back to direct execution rather than crashing or silently
  producing wrong output.

## Implementation Phases

### Phase 1 — Scaffolding (no behavior change)
Create the four core abstractions as empty shells:
`PipelineContext`, `StageResult`/`StageStatus`, `Stage` base class,
`Pipeline` runner. No stages yet. All tests should still pass.

Files: `pipeline_context.py`, `stage_result.py`, `stage_base.py`,
`pipeline.py`

### Phase 2 — Routing and inline answer stages
Implement `RoutingStage` and `DirectInlineStage`. Extract
`_build_routing_system`, `_parse_routing_response`, and helpers into
`src/runtime/utils.py`. Wire a minimal pipeline with just these two
stages into `Agent.__init__` alongside the existing `call()` (not
replacing it yet — both paths live together during transition).

Files: `stages/routing.py`, `runtime/utils.py`

### Phase 3 — Workflow matching and planning stages
Implement `WorkflowMatchStage` and `PlanningStage`. These two stages
replace the workflow routing block and planner call in `call()`.

Files: `stages/workflow_match.py`, `stages/planning.py`

### Phase 4 — Correction and validation stages
Implement `EntityCriticStage` and `ValidatorStage`. `ValidatorStage`
holds a `Planner` reference and handles the replan-then-revalidate retry
loop internally. This eliminates the current inline retry at lines
283–291 of `call()`.

Files: `stages/entity_critic.py`, `stages/validator.py`

### Phase 5 — Council stage
Implement `CouncilStage`. Move `_strip_challenged_steps` to a
module-level function in `stages/council.py`. Wire the workflow-bypass
logic (skip critic for workflow-generated plans) into the stage's input
gate.

Files: `stages/council.py`

### Phase 6 — Execution and synthesis stages
Implement `ExecutionStage` (lift of `_execute_plan` + `_run_step`) and
`SynthesizerStage`. These are the largest stages by line count but the
most mechanical to extract — behavior is identical to current code.

Files: `stages/execution.py`, `stages/synthesizer.py`

### Phase 7 — Direct execution stage + pipeline cutover
Implement `DirectExecutionStage` (lift of `_run_loop`). Assemble the
full pipeline in `Agent.__init__` via `build_pipeline()`. Replace
`call()` body with the 5-line version. Delete the now-dead methods from
`agent.py` (`_execute_plan`, `_run_step`, `_run_loop`,
`_strip_challenged_steps`, `_step_system`, `_step_utility_tools`).

This is the cutover phase — after this, the old `call()` is gone and the
pipeline is live.

Files: `stages/direct_execution.py`, `agent.py`

### Phase 8 — Stage gate hardening
With the pipeline live, add the stage gate checks that were not possible
before (because there was no enforcement point between stages):
- `EntityCriticStage`: detect obviously bad corrections (corrected path
  is a single word, not a path, or came from a tool result) and return
  `ASK_USER` with a confirmation question instead of silently applying
- `CouncilStage`: if all steps are stripped and the plan becomes
  incoherent (e.g. a synthesis step with nothing to synthesize), return
  `ABORT` with a clear reason rather than executing a broken 1-step plan
- `ValidatorStage`: surface validation failures to the user in the ABORT
  message rather than silently falling back to direct execution

Files: `stages/entity_critic.py`, `stages/council.py`,
`stages/validator.py`

## Phase Summary

| Phase | What | Key outcome |
|-------|------|-------------|
| 1 | Scaffolding | Core types exist, importable, no behavior change |
| 2 | Routing stages | Utils extracted, routing pipeline wired in parallel |
| 3 | Workflow + planning | Plan generation in pipeline |
| 4 | Correction + validation | Entity critic and validator with retry loop |
| 5 | Council | Critic review in pipeline, workflow bypass correct |
| 6 | Execution + synthesis | Full plan execution in pipeline |
| 7 | Cutover | Old `call()` deleted, pipeline is live |
| 8 | Gate hardening | ASK_USER and ABORT gates enforce stage contracts |

## Success Criteria

After Phase 7:
- All existing session behaviors reproduce identically
- `agent.py` `call()` is ≤ 10 lines
- No logic remains in `call()` — it only builds context and runs pipeline
- Session log output is identical (same banners, same log lines)

After Phase 8:
- Entity critic corruption (e.g. `encryption/decryption` substitution)
  either raises `ASK_USER` or is silently suppressed rather than applied
- Council stripping all steps produces ABORT + direct execution fallback
  rather than a broken 2-step plan
- Each stage failure has a distinct, logged reason visible in session logs
