# 0079d — Phase D: ContinuationStage Architecture (Design)

> **Read first:** `_plans/0079-runtime-as-god.md` §0, §1.2, §4.
> This phase ships **no code**. It is the design that 0079e implements
> and that 0079f, 0079g, 0079h, 0079i depend on.
> Phases 0079a, 0079b, 0079c must be merged before this phase makes
> sense to read in execution context.

## 1. Purpose

`ContinuationStage` is the runtime component that owns the question:
**"Are we done with the user's task?"**

It is inserted between `ExecutionStage` and `SynthesizerStage`:

```
ExecutionStage  →  ContinuationStage  →  SynthesizerStage
                        │
                        ├── decision: SYNTHESIZE → fall through to Synthesizer
                        ├── decision: DONE       → return DONE (no synthesis needed)
                        └── decision: LOOP       → generate continuation plan,
                                                    loop back through ExecutionStage
```

It replaces the static `Plan.requires_synthesis` field with a **dynamic,
post-execution decision**. It also enables true **task-level loops**
(write → verify → fix → verify) that the brief calls "the fix loop we've
been fighting."

This addresses **DRIFT-2**, **DRIFT-8**, and **DRIFT-9**.

## 2. Decision model

After every `ExecutionStage` run, `ContinuationStage.run` produces one
of three decisions:

| Decision    | What the pipeline does                                                |
|-------------|------------------------------------------------------------------------|
| `SYNTHESIZE` | Stage returns `OK`. Pipeline runner advances to `SynthesizerStage`.  |
| `DONE`       | Stage returns `DONE`. Pipeline runner returns `context.response` immediately. |
| `LOOP`       | Stage generates a continuation plan via `Planner.replan` (or a skill replay), assigns it to `context.plan`, returns `OK`. **The pipeline runner needs a small enhancement** to send control flow back to ExecutionStage in this case (see §6). |

```python
# src/runtime/stages/continuation.py (file lives in 0079e)
class ContinuationDecision(str, Enum):
    SYNTHESIZE = "synthesize"
    DONE       = "done"
    LOOP       = "loop"
```

## 3. Inputs the decision is computed from

Per the runtime-as-god principle, ContinuationStage has **maximum
visibility**. It reads:

- `context.plan` — the just-executed plan, with all `Step.result` populated
- `context.plan.steps` — including any failed/skipped/deferred steps
- `context.user_message` — the original user request
- `context.continuation_state` — bookkeeping (iteration counter, history)
  added to `PipelineContext` (see §5)
- the active **skill's `CompletionCriteria`** if the plan was generated
  by a single skill (added in phase 0079g; until then, the LLM-judgment
  fallback alone is used)

It does NOT read:
- `context.routing_path` — neutralized in phase 0079b
- `Plan.requires_synthesis` — deleted in phase 0079f
- any `Step.flags.*` prescriptive fields

## 4. Decision algorithm

```
def decide(context) -> ContinuationDecision:
    state = context.continuation_state

    # ── Hard stops ───────────────────────────────────────────────────
    if state.iteration_count >= config.max_iterations:
        return SYNTHESIZE        # cap reached — best to summarize what we have

    # ── Structural completion (cheap, no LLM) ─────────────────────────
    criteria = _active_completion_criteria(context.plan)
    if criteria is not None:
        outcome = criteria.evaluate(context)   # → CRITERIA_MET | CRITERIA_NOT_MET | CRITERIA_INCONCLUSIVE
        if outcome == CRITERIA_MET:
            return _met_to_decision(criteria.on_met)   # SYNTHESIZE or DONE
        if outcome == CRITERIA_NOT_MET:
            return LOOP   # explicitly known not done — loop without LLM call
        # INCONCLUSIVE falls through to LLM judgment

    # ── LLM judgment (one focused call) ───────────────────────────────
    judgment = _llm_judge(context)   # → DONE | NEED_MORE | TRIVIAL
    if judgment == TRIVIAL:
        return DONE             # no synthesis needed (e.g. single-tool answer self-explanatory)
    if judgment == DONE:
        return SYNTHESIZE
    return LOOP
```

`_active_completion_criteria(plan)` returns the criteria of a skill
**iff the plan was a single-skill plan** (i.e., one expanded skill plus
optional pre/post conversation steps). Multi-skill plans default to
LLM judgment because aggregating per-skill criteria is its own design
problem (deferred).

`_met_to_decision` maps a skill's "done" outcome to either SYNTHESIZE
or DONE, controlled by the skill's criteria definition.

## 5. New `PipelineContext` fields

```python
# src/runtime/pipeline_context.py — additions for phase 0079e

# ── Continuation state (managed by ContinuationStage) ────────────────
@dataclass
class ContinuationState:
    iteration_count: int = 0
    last_decision: str | None = None            # ContinuationDecision value
    artifacts_carried: list[str] = field(default_factory=list)  # artifact keys to expose to next iteration
    history: list[dict] = field(default_factory=list)
    # Each history entry: {"iteration": int, "plan_steps": int, "decision": str, "reason": str}

# Field on PipelineContext:
continuation_state: ContinuationState = field(default_factory=ContinuationState)

# Already exists; ContinuationStage may overwrite when LOOP:
plan: Plan | None = None
```

The `history` list lets the LLM judgment include "we already tried
this approach last iteration" without re-deriving it.

## 6. Pipeline runner enhancement

Today `Pipeline.run` (`src/runtime/pipeline.py:48-78`) walks stages
linearly. To support the LOOP decision, ContinuationStage needs to
"jump back to ExecutionStage." Two options were considered:

**Option A — internal loop in ContinuationStage.** Stage returns OK
after running ExecutionStage logic itself in a loop. Rejected: violates
the principle that a stage does one thing and the pipeline orchestrates.

**Option B — runner understands a JUMP transition.** Add a new
`StageStatus.JUMP_TO` (or similar) that the runner interprets by
resetting `idx` to a labeled stage. Acceptable but adds a transition
type to a deliberately small set.

**Chosen — Option C: a single re-execute hook on ContinuationStage.**
ContinuationStage owns a reference to ExecutionStage and, on LOOP,
calls `self._execution_stage.run(context)` directly inside its own
`run` method, then re-evaluates. The pipeline runner sees only `OK` /
`DONE`. This keeps `StageStatus` unchanged and matches the
runtime-as-god principle that infrastructure orchestrates.

Concrete shape:

```python
class ContinuationStage(Stage):
    name = "ContinuationStage"

    def __init__(self, planner, execution_stage, evaluator, ...):
        self._planner = planner
        self._execution = execution_stage    # injected reference for loop-back
        self._evaluator = evaluator          # the LLM judgment helper
        ...

    def run(self, context: PipelineContext) -> StageResult:
        while True:
            decision = self._decide(context)
            context.continuation_state.last_decision = decision.value
            context.continuation_state.history.append({
                "iteration": context.continuation_state.iteration_count,
                "plan_steps": len(context.plan.steps) if context.plan else 0,
                "decision": decision.value,
                "reason": ...,
            })

            if decision == ContinuationDecision.SYNTHESIZE:
                return StageResult(status=StageStatus.OK, updated_context=context)

            if decision == ContinuationDecision.DONE:
                return StageResult(status=StageStatus.DONE, updated_context=context)

            # LOOP — generate continuation plan, run execution again
            new_plan = self._build_continuation_plan(context)
            if new_plan is None:
                logger.info("  continuation: cannot produce continuation plan — synthesizing instead")
                return StageResult(status=StageStatus.OK, updated_context=context)

            context.plan = new_plan
            context.continuation_state.iteration_count += 1
            self._execution.run(context)
            # Loop back to top — re-decide.
```

**Note:** `ExecutionStage.run` already mutates context in place
(it sets `context.response`), so re-entering it with a new plan is
safe. ExecutionStage already always returns `OK` (or `DONE` when
`requires_synthesis=False` — that branch is deleted in phase 0079f).

After phase 0079f, ExecutionStage always returns `OK` and ContinuationStage
is the sole authority over what happens next.

## 7. Building a continuation plan

When `ContinuationStage` decides LOOP, it must produce a new plan to
hand to ExecutionStage. Three tiers:

1. **Skill replay (cheapest).** If the original plan was a single-skill
   expansion and the skill declares itself "loopable", ask the skill
   to emit *delta* steps for the next iteration. Skill API:

   ```python
   class Skill:
       def continuation_steps(self, ctx: SkillContext, prior_results: list[Step]) -> list[Step] | None:
           """Return steps for the next iteration, or None if not loopable."""
           return None
   ```

   For `test-reconstruction`, this is "fix the source and re-run
   diff_behavior." The skill knows the structure of its loop.

2. **Planner replan.** When skill replay returns None or the original
   plan wasn't a single skill, call `Planner.replan(plan, last_step,
   reason)` (existing API at `src/planning/planner.py:191-245`). The
   reason is filled with what the LLM judgment said is missing.

3. **Bail.** If both fail, return None from `_build_continuation_plan`,
   let ContinuationStage fall through to SYNTHESIZE.

## 8. The LLM judgment call

One focused chat completion. Prompt structure:

```
SYSTEM:
You are evaluating whether an autonomous agent has finished the user's task.
Respond with strict JSON:
{
  "judgment": "done" | "need_more" | "trivial",
  "reason": "<one sentence>",
  "missing": "<what is still needed, if any — empty for done/trivial>"
}

"done"      — the user's request appears fully addressed by the executed plan.
"need_more" — there is a clear unmet requirement; specify it in 'missing'.
"trivial"   — the executed plan answered the user directly; no synthesis is
              needed (e.g. a single-tool query whose result speaks for itself).

USER:
Original request: {user_message}

Executed plan ({n_steps} steps):
{plan_summary}

Iteration {iteration_count} of max {max_iterations}.
{prior_history_lines_if_any}
```

The prompt is deliberately small. For most queries it's a single-shot
classification at the cheap "runtime LLM" tier (same provider used
by the monitor/importance scorer).

`plan_summary` reuses `Plan.summary()` (already implemented at
`src/planning/schema.py:188-196`).

## 9. Iteration cap & escalation

`config.runtime.continuation.max_iterations` (default `5`).
On reaching the cap, ContinuationStage returns SYNTHESIZE with a
log line that says the cap was hit. Future enhancement: ESCALATE
to user when cap reached if confidence is low; deferred.

## 10. Context propagation between iterations

When LOOP decides to run another plan, useful state from prior steps
must be visible to the next plan. The mechanism is the **artifact store**:

- ExecutionStage already records artifacts via tools that call
  `store_artifact`.
- ContinuationStage adds the artifact keys produced by the just-finished
  iteration to `continuation_state.artifacts_carried`.
- `Planner.replan` is given a "carried artifacts" block in its user
  turn (one short list with key + summary). The planner can reference
  those keys in later steps.

This avoids stuffing raw step results into prompts and reuses an
existing infrastructure surface.

## 11. Interaction with Monitor (forward reference)

Phase **0079h** adds `StepDecision.GOAL_ACHIEVED` to the monitor.
When the monitor returns GOAL_ACHIEVED mid-execution, `ExecutionStage`
short-circuits the rest of its plan steps. `ContinuationStage` then
sees a partial plan that ended early — its decision logic must be
robust to "fewer steps completed than queued." The skill-criteria
evaluator and the LLM judgment both already work from results, not
from "all steps complete," so this is naturally compatible.

## 12. Configuration

```python
# src/config.py — add ContinuationConfig (phase 0079e wires it in)
@dataclass
class ContinuationConfig:
    enabled: bool = True
    max_iterations: int = 5
    llm_judge_label: str = "ContinuationStage"
    # When set, single-skill plans skip the LLM judge and rely on the
    # skill's CompletionCriteria. Default False — LLM judge still runs
    # as a sanity check.
    trust_skill_criteria: bool = False
```

In `RuntimeConfig`:

```python
continuation: ContinuationConfig = field(default_factory=ContinuationConfig)
```

## 13. Failure modes and how the design handles them

| Failure                                              | How the design handles it                                                       |
|------------------------------------------------------|---------------------------------------------------------------------------------|
| LLM judge returns malformed JSON                     | Default to SYNTHESIZE (safe — produces a response).                             |
| Continuation plan generation fails                   | Fall through to SYNTHESIZE.                                                     |
| ExecutionStage raises during a re-entry              | ContinuationStage catches, logs, returns OK (synth runs on partial).            |
| Iteration cap reached                                | Return SYNTHESIZE; log explicitly.                                              |
| Skill criteria say MET but LLM judge says NEED_MORE  | Skill criteria win unless `trust_skill_criteria=False` AND the LLM judge has a confident `missing` field. Default behavior: require both signals to be MET, otherwise loop. |
| Plan has no executed steps (zero-step plan)          | Treat as trivial — return DONE.                                                 |

## 14. What this design does NOT do

- It does NOT run multiple plans in parallel.
- It does NOT modify the existing in-execution `REPLAN` mechanism in
  ExecutionStage (`_execute_plan` lines 337-368). That handles
  *step-level* failures; ContinuationStage handles *plan-level*
  loops. Keep them separate.
- It does NOT introduce a new persistence model. Each iteration's
  plan is recorded by `PersistenceWriter.record_plan` (already invoked
  on every replan inside ExecutionStage); ContinuationStage just bumps
  the `replan_count` semantics by re-entering ExecutionStage.

## 15. Open questions deliberately deferred

- **Aggregating CompletionCriteria across multiple skills in one plan.**
  For now, multi-skill plans always defer to the LLM judge. A future
  phase can introduce a CompletionPolicy that combines criteria.
- **Streaming during continuation.** The LLM judge is non-streaming.
  The synthesizer (final stage) preserves its existing streaming
  behavior. No change needed for now.
- **User-visible "still working" updates between iterations.** Today
  the spinner restarts inside ExecutionStage. ContinuationStage updates
  the spinner with iteration-count text on each LOOP. No prompt UI
  change.

## 16. Summary diagram

```
                ┌────────────────────────┐
                │     ExecutionStage     │
                └───────────┬────────────┘
                            │
                            ▼
                ┌────────────────────────┐
                │   ContinuationStage    │
                │  ┌──────────────────┐  │
                │  │ decide()         │  │
                │  │  - criteria eval │  │
                │  │  - LLM judge     │  │
                │  └────────┬─────────┘  │
                └───────────┼────────────┘
                  ┌─────────┼─────────┐
                  │         │         │
                  ▼         ▼         ▼
              SYNTHESIZE   DONE      LOOP
                  │         │         │
                  │         │         ▼
                  │         │   build continuation plan
                  │         │         │
                  │         │         ▼
                  │         │   re-enter ExecutionStage
                  │         │         │
                  │         │         └──► back to ContinuationStage
                  │         │
                  ▼         ▼
            SynthesizerStage (return DONE response)
```

Implementer next opens `0079e-continuation-impl.md`.
