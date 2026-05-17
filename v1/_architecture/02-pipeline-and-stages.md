# 02 — Pipeline and stages

How a single user message becomes a response: which stages run in what
order, what each one does, and what invariants hold between them.

## Stage ordering

`src/agent.py:_build_pipeline` constructs:

```
RagContextStage
RoutingStage             [runtime scope]
DirectInlineStage        (DONE if clean conversational answer)
SkillHintStage           [runtime scope]
PlanningStage            (calls PlanValidator inline pre-expansion)
SkillExpansionStage
EntityCriticStage
ValidatorStage           (PlanValidator post-expansion)
CouncilStage
ExecutionStage           (loops with monitor + replan)
ContinuationStage
SynthesizerStage
DirectExecutionStage     (ABORT fallback)
```

## What each stage does

| Stage | Role | Returns |
|---|---|---|
| `RagContextStage` | Pull relevant chunks from session + global RAG; populate `context.rag_context` | OK |
| `RoutingStage` | One LLM call: classify direct vs plan + (for direct) produce inline answer | OK |
| `DirectInlineStage` | DONE if routing returned a clean inline answer; else OK | OK / DONE |
| `SkillHintStage` | WorkflowSelector picks a skill the planner *might* want to use | OK |
| `PlanningStage` | Planner generates plan; PlanValidator runs (pre-expansion, defers rule 7 if a skill step is present) | OK / RETRY / ABORT |
| `SkillExpansionStage` | Replace `skill:foo` steps with concrete steps from `Skill.expand()`. Re-numbers | OK |
| `EntityCriticStage` | Lightweight LLM check for entity references that don't match plan | OK |
| `ValidatorStage` | PlanValidator post-expansion (re-checks rule 7: write_file requirement) + logs final plan | OK / ABORT |
| `CouncilStage` | (Optional) multi-agent critic vote on plan quality | OK |
| `ExecutionStage` | Step-by-step ReAct loop with monitor between steps; handles RETRY / REPLAN / DEFER / SKIP / GOAL_ACHIEVED / ESCALATE | OK / DONE |
| `ContinuationStage` | Should we loop back to execution for more work? Owns task-level completion criteria | OK / DONE |
| `SynthesizerStage` | Final assembly of the response | DONE |
| `DirectExecutionStage` | ABORT fallback — free-form tool loop when planning broke down | DONE |

## StageResult semantics

| Status | Meaning |
|---|---|
| `OK` | Continue to next stage |
| `DONE` | Skip remaining stages (response is set) |
| `RETRY` | Re-run current stage (subject to per-stage cap) |
| `ASK_USER` | Pause for user input; resume current stage with answer |
| `ABORT` | Stop pipeline; run fallback (DirectExecutionStage) |

The pipeline (`src/runtime/pipeline.py`) enforces caps and drives the
fallback. Stages never decide what happens next — they return state and
the pipeline acts on it (runtime-as-god, doc 01).

## Scope conventions

Stages enter the appropriate `runtime.scope`:

- `RoutingStage`, `SkillHintStage`, `ExecutionMonitor` (called inside
  ExecutionStage's loop), `ImportanceScorer` (same) — wrap their LLM
  call in `with scoped(RUNTIME):`.
- Other stages stay in the default `MAIN` scope.

The scope drives AFM budget selection, log tagging, and event
`agent_scope` stamping (see doc 03 + 05).

## Context flow

```
PipelineContext flows through every stage. Notable fields:

  user_message            (the input)
  classification          (set by RoutingStage)
  skill_hint              (set by SkillHintStage)
  plan                    (set by PlanningStage, mutated by SkillExpansionStage)
  packed_messages         (set by RoutingStage from context_mgr.pack)
  rag_context             (set by RagContextStage)
  response                (set by SynthesizerStage or DirectInlineStage)
  failure_reason          (set on ABORT)
  active_skill_name       (set by SkillExpansionStage; consumed by ContinuationStage)
  continuation_state      (loop tracking for ContinuationStage)
  on_token                (token-stream callback from frontend)
  _pause_check            (cooperative-cancel callback from frontend)
```

## Sub-agent dispatch sits at tool layer, not stage layer

`SubAgentTool` is a regular `BaseTool` subclass. When the agent's plan
includes a `tool="subagent_<name>"` step, ExecutionStage runs it like
any other tool. The runner (`runtime.subagents.runner.SubAgentRunner`)
spawns a child Agent that runs its OWN pipeline (recursive
instantiation of all the above stages, scoped to the child).

So the pipeline doesn't grow a new stage type for sub-agents — they
just look like tools to the planner. The narrowed child registry +
scope contextvar handle the rest. See doc 04.

## Related plans

- `_plans/0083-decoupled-tui.md` — service layer + pipeline-stage
  cooperation
- `_plans/0089-pluggable-context-manager.md` — how stages pass context
  through AFM
- `_plans/0090-context-discipline-and-subagents.md` §6 — scope-aware
  budgets at stage entry points
