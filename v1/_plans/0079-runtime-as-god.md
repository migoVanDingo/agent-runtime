# 0079 — Runtime Infrastructure as God: Drift Elimination + ContinuationStage

> **Audience:** This document and its sibling phase docs (`0079a` … `0079i`)
> are written for an implementer (Sonnet) who has full read access to the
> codebase but no prior context. Each phase doc is self-contained with file
> paths, line numbers, before/after snippets, and a verification checklist.
> Read this design doc once, then execute the phases in order.

---

## 0. The Core Tenet (read every phase against this)

The **Runtime Infrastructure** (`src/runtime/`) is the universe.
It is the **only** layer with authority over:

| Decision                                          | Owner                              |
|---------------------------------------------------|------------------------------------|
| When execution pauses (escalation to user)        | runtime (`escalation`, `user_gate`) |
| When the future is rewritten (replanning)         | runtime (Pipeline + Planner.replan) |
| When a step is retried / reconsidered             | runtime (Monitor → ExecutionStage) |
| When a plan is accepted / rejected                | runtime (Council)                  |
| Which tools are available at any moment           | runtime (router + registry)        |
| Whether the *task* is complete                    | runtime (**ContinuationStage** — new) |
| Whether the goal is achieved                      | runtime (**ContinuationStage** — new) |

Everything else — tools, workflows, providers, planner, synthesizer — is a
**passive participant**. Components do work and return results.
**Components do not make infrastructure decisions.**

Three rules follow from this:

1. **Plan metadata is descriptive, never prescriptive.** A `Plan`/`Step`
   describes WHAT to do; it does NOT tell the runtime HOW to behave
   (no `retry`, `escalate`, `defer`, `requires_synthesis`).
2. **Skills are passive building blocks.** They declare what they do,
   what they need, and what "done" looks like. They do not contain loop
   counts, capability detection, or runtime policy.
3. **Stages own all control flow.** A stage may *consult* metadata, but
   the decision is the stage's. No stage changes its behavior based on
   another stage's bookkeeping (e.g., `routing_path`).

When in doubt while implementing any phase: ask "would this decision
survive if I deleted the metadata it depends on?" If yes, the runtime
is in charge. If no, you've found drift.

---

## 1. Pipeline shape (current and target)

### 1.1 Current pipeline (verified against `src/agent.py:53-95`)

```
RoutingStage
  → DirectInlineStage           [DONE if clean inline answer]
  → WorkflowMatchStage          [may produce a complete Plan]
  → PlanningStage               [runs only if plan still None]
  → EntityCriticStage
  → ValidatorStage
  → CouncilStage                [bypassed for workflow plans via _WORKFLOW_PATHS]
  → ExecutionStage              [runs Plan; DONE if !requires_synthesis]
  → SynthesizerStage            [no-op if !requires_synthesis]
  → DirectExecutionStage        [fallback only — also last in list]
```

### 1.2 Target pipeline (after this initiative)

```
RoutingStage
  → DirectInlineStage           [DONE if clean inline answer]
  → WorkflowMatchStage          [now: advisory hint only — never produces plans]
  → PlanningStage               [always produces the plan]
  → SkillExpansionStage         [NEW — inlines skill calls into concrete steps]
  → EntityCriticStage
  → ValidatorStage
  → CouncilStage                [explicit policy — no provenance bypass]
  → ExecutionStage              [runs Plan; always returns OK]
  → ContinuationStage           [NEW — owns "are we done?"; loops back to Execution]
  → SynthesizerStage            [runs only when ContinuationStage said synthesize]
  → DirectExecutionStage        [unchanged fallback]
```

The **two new stages** (`SkillExpansionStage`, `ContinuationStage`) and
the **one demoted stage** (`WorkflowMatchStage` → advisory) are the
structural delta.

---

## 2. Phase map (execute in this order)

| # | File              | What it does                                  | Drift addressed       | Depends on |
|---|-------------------|-----------------------------------------------|-----------------------|------------|
| a | `0079a-tool-selection.md`     | Unify three-tier tool selection in `ExecutionStage`; data-drive utility tools | DRIFT-4 | — |
| b | `0079b-council-bypass.md`     | Replace `_WORKFLOW_PATHS` with explicit risk/complexity criteria | DRIFT-5 | — |
| c | `0079c-skills-system.md`      | Define Skill interface; demote `WorkflowSelector` to advisory; add `SkillExpansionStage`; refactor existing workflows | DRIFT-7 (most), DRIFT-1 (partial) | — |
| d | `0079d-continuation-arch.md`  | **Design** the ContinuationStage (data model, decision LLM, iteration policy, context propagation) | sets up DRIFT-2, DRIFT-6, DRIFT-8, DRIFT-9 | a, b, c |
| e | `0079e-continuation-impl.md`  | **Implement** ContinuationStage as pass-through evaluator wired into pipeline | DRIFT-8, DRIFT-9 | d |
| f | `0079f-schema-cleanup.md`     | Remove `requires_synthesis`; retire prescriptive `StepFlags`; collapse `Step.flags` to runtime-state-only | DRIFT-2, DRIFT-3 | e (must own evaluation first) |
| g | `0079g-completion-criteria.md` | Skills declare `CompletionCriteria`; ContinuationStage evaluates | replaces last of DRIFT-2 | c, e |
| h | `0079h-monitor-enhancement.md` | Monitor reads plan-level goal context; new `GOAL_ACHIEVED` decision | DRIFT-6 | g |
| i | `0079i-fix-loop-migration.md` | Worked example: migrate `test-reconstruction` to a skill + ContinuationStage loop | DRIFT-1 (rest) | all of the above |

**Why this order?** Phases (a) and (b) are independent local fixes — safe
quick wins. Phase (c) re-shapes how plans are sourced. Phase (d) is just a
design doc — no code. Phase (e) introduces the new stage as a *pass-through*
that returns `OK` without doing real work; this lets the stage sit in the
pipeline before we depend on it. Phase (f) is the schema demolition — we
can only delete `requires_synthesis` once `ContinuationStage` is in place
to do its job. Phase (g) gives skills a `CompletionCriteria` field so
ContinuationStage has something to evaluate. Phase (h) lets the Monitor
short-circuit a plan when the goal is met early. Phase (i) puts everything
together on the canonical fix-loop case.

DRIFT-10 (container `runs_locally` policy) is **not** in this initiative.
It will become its own future plan since it touches the sandbox subsystem
and is not on the critical path for runtime-as-god. Note it as follow-up.

---

## 3. Catalogued drift → phase mapping

For each drift item from `0078-opus-refactor-brief.md`, here is the
exact resolution location:

- **DRIFT-1** (loop control in step descriptions) → resolved in **0079c** (workflow→skill thinning) and **0079i** (fix-loop migration takes the iteration count out of `test_reconstruction.py:85-86` and into `ContinuationStage` config + skill `CompletionCriteria`).
- **DRIFT-2** (`requires_synthesis` static evaluation) → resolved in **0079e** (ContinuationStage decides) and **0079f** (field deleted from `Plan`).
- **DRIFT-3** (`StepFlags.retry/escalate/defer` prescriptive) → resolved in **0079f**. Note: these booleans are *currently never read* by execution.py (only `retry_count`, `deferred`, `skipped` are), so the cleanup is straightforward.
- **DRIFT-4** (three-tier tool selection) → resolved in **0079a**.
- **DRIFT-5** (council bypass via `routing_path`) → resolved in **0079b**.
- **DRIFT-6** (monitor only sees step) → resolved in **0079h**.
- **DRIFT-7** (workflow `generate_plan` does too much) → resolved in **0079c**.
- **DRIFT-8** (no pipeline-level continuation) → resolved in **0079e**.
- **DRIFT-9** (synthesizer makes no decisions) → addressed in **0079e** (ContinuationStage decides whether synth runs at all).
- **DRIFT-10** (container isolation policy) → **out of scope for 0079 series; track as 0080+ follow-up.**

---

## 4. Glossary (used consistently across phase docs)

- **Skill** — a passive, named building block that knows how to accomplish a
  bounded sub-goal (e.g., `deep-disassembly`, `test-reconstruction`).
  A skill exposes: a name, an intent string, an `expand()` method that
  returns concrete `Step`s, and `CompletionCriteria` that say what "done"
  looks like for that skill. Skills do *not* check capabilities, run loops,
  or set runtime flags.
- **Workflow** — the pre-existing concept being retired/renamed to "skill".
  When a phase doc says "rename" it means: file path, class name, and
  registry constant change as part of phase 0079c.
- **`SkillExpansionStage`** — new pipeline stage between `PlanningStage`
  and `EntityCriticStage`. Walks plan steps; any step whose `tool` field
  has the form `skill:<name>` is replaced inline by the steps that skill
  emits. Re-numbers steps. Idempotent on plans without skills.
- **`ContinuationStage`** — new pipeline stage between `ExecutionStage`
  and `SynthesizerStage`. Decides one of: `synthesize`, `loop`, `done`.
  Owns the iteration counter. Generates a continuation plan via
  `Planner.replan()` when looping. Replaces `requires_synthesis`.
- **`CompletionCriteria`** — declarative description of "done" attached
  to a skill. Two shapes (B.3 details): structural (e.g. `all_match=true`
  in last `diff_behavior` result) or LLM-judged (free-form prompt).
- **Provenance** — how a plan came to exist. After 0079c, only one
  provenance exists: `planner` (with optional pre-seeded `skill_hint`
  metadata). `routing_path` is no longer load-bearing.

---

## 5. Invariants every phase must preserve

While executing any phase, the following must remain true after each
phase lands:

1. `pytest` passes (whatever exists in the repo).
2. `python -m src.main` (or however the agent is launched) starts
   without errors and answers a trivial direct-mode question.
3. The canonical "deep-disassembly + test-reconstruction" flow (the
   thing the user actually runs) still produces a non-empty response —
   even if quality temporarily regresses on intermediate phases.
4. No stage references a metadata field as load-bearing without
   that field being defined in `Plan`/`Step`/`PipelineContext`.
5. `Pipeline.run` remains the single entrypoint; no stage spawns
   another pipeline.

If a phase appears unable to satisfy these invariants, **stop and
escalate**: the phase plan is wrong, not the invariants.

---

## 6. What is NOT in scope

- **Observability/metrics initiative.** The brief mentions runtime
  awareness of "context, tokens, current tasks and overall goals at every
  point" with "reproducible metrics" and visualizable output. This is
  deliberately deferred to a future plan (likely `0080-runtime-observability.md`).
  Phases here may add small structured logs where convenient, but no
  phase introduces a metrics export surface. If a phase plan tells you
  to "wire telemetry," push back.
- **Provider/model changes.** No swapping models; no SDK upgrades.
- **Tool ecosystem changes.** Tools are passive. We touch
  `_step_utility_tools` (data-drive it) and that is the only tool-layer
  change.
- **Persistence schema changes.** `PersistenceWriter.record_plan` and
  `record_step` remain compatible. If a phase needs to record new state
  (e.g., continuation iterations), it adds a new record method, never
  changes existing ones.
- **Container `runs_locally` (DRIFT-10).** Out of scope.

---

## 7. Reading order for the implementer

1. This file.
2. `_plans/0078-opus-refactor-brief.md` (the original brief — for
   philosophical alignment and the drift catalogue).
3. The next phase doc you're about to execute.

When a phase doc says "see §N of 0079," it means this file. Phase docs
do not duplicate this content; they reference it.

---

## 8. Verification gate at the end of phase (i)

After all 9 phases land, the following must be true. If any is false,
the initiative is not complete.

- [ ] `Plan` has no `requires_synthesis` field.
- [ ] `Step.flags` exists only as runtime state; planner/skills never set
      `retry`/`escalate`/`defer` (the fields no longer exist on the
      JSON schema or the dataclass).
- [ ] `WorkflowMatchStage` no longer sets `context.plan`. It at most
      sets `context.skill_hint` (advisory) for the planner to read.
- [ ] `_WORKFLOW_PATHS` is gone from `council.py`. Council decides via
      explicit risk/complexity policy.
- [ ] `ExecutionStage._step_utility_tools` is removed; utility-tool
      relations live in config (`config.runtime.tool_policy.utility_tools`).
- [ ] `ContinuationStage` exists in `src/runtime/stages/continuation.py`
      and is wired into `src/agent.py` between Execution and Synthesizer.
- [ ] `SkillExpansionStage` exists in `src/runtime/stages/skill_expansion.py`
      and is wired between Planning and EntityCritic.
- [ ] At least one skill (`test-reconstruction`) has a populated
      `CompletionCriteria` and demonstrates a fix-loop driven entirely by
      ContinuationStage with no iteration count in any step description.
- [ ] `ExecutionMonitor` can return `StepDecision.GOAL_ACHIEVED` and
      ExecutionStage handles it (short-circuits remaining steps).
- [ ] `pytest` passes; the agent answers a direct question end-to-end.

---

## 9. Naming conventions used in phase docs

- File paths are absolute starting from the repo root: `src/runtime/...`.
- Line numbers are given as `path:line` (e.g., `src/planning/schema.py:170`).
  Numbers were verified against the codebase as of `git log -1 main`
  (commit `0a9a064`); if Sonnet finds a mismatch, **read the file and
  trust what's there**, then update the patch context accordingly.
- "Before/After" code blocks show only the surrounding lines needed for
  the patch to be unambiguous.
- "Verification" sections at the end of each phase doc list concrete
  shell or pytest commands; if those commands aren't appropriate for
  the change, the phase doc says so.

Now go to `0079a-tool-selection.md`.
