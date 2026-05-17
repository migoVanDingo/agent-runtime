# 0078 — Opus Briefing: Runtime Infrastructure Refactor + Continuation Stage

## Who You Are and What You're Doing

You are Claude Opus. You are being asked to generate a complete set of numbered
implementation plan documents for a two-part initiative:

1. **Refactor** the agent-runtime codebase to eliminate architectural drift
2. **Design and implement** a new ContinuationStage in the pipeline

Your output will be a set of plan files (following the existing `_plans/` naming
convention: `0079-design.md`, `0079a-phase-a.md`, `0079b-phase-b.md`, etc.) that
a developer will follow to execute the work. Each plan must be precise, reference
specific files and line numbers, and define exactly what changes to make and why.

The codebase is at: `/Users/bubz/Developer/agent/runtime/agent-runtime/src/`

Read any files you need before writing plans. Do not guess at details.

---

## The Core Paradigm: Runtime Infrastructure as God

This is the foundational design principle of the system. It must guide every
decision in your plans.

**The Runtime Infrastructure (`src/runtime/`) is the universe.** It is the only
layer with authority over:

- When execution pauses (escalation to user — "stop time")
- When the future changes (replanning — "rewrite the future")
- When something is retried or reconsidered (retry/review — "change the past")
- Whether a plan is accepted or rejected (council/validation)
- Which tools are available at any moment
- Whether the task is complete
- Whether the goal has been achieved and requirements satisfied

Everything else — tools, workflows, providers, the planner, the synthesizer — is
a **passive participant** that the infrastructure orchestrates. These components
do work and return results. They do not make infrastructure decisions.

**The drift problem:** Over time, infrastructure decisions have leaked into other
components. Workflows encode loop control in step descriptions. The planner
pre-selects tools and retry policy. Plan metadata (a static artifact) controls
dynamic runtime behavior. This creates coupling, reduces agility, and makes the
system harder to reason about.

---

## System Overview: How It Currently Works

### The Pipeline

The pipeline is a linear sequence of stages. Each stage receives a
`PipelineContext`, does its work, and returns a `StageResult` with a status:

- `OK` → advance to next stage
- `DONE` → short-circuit, return response immediately
- `RETRY` → re-run this stage with `failure_reason` injected
- `ASK_USER` → prompt user, append response, re-run
- `ABORT` → skip remaining stages, run DirectExecutionStage as fallback

**Current stage sequence:**
```
RoutingStage
  → DirectInlineStage (short-circuit for conversational direct-mode)
  → WorkflowMatchStage
  → PlanningStage
  → EntityCriticStage
  → ValidatorStage
  → CouncilStage  (adversarial plan review)
  → ExecutionStage  (runs the plan step by step)
  → SynthesizerStage  (generates final response from step results)
  → DirectExecutionStage  (fallback if ABORT)
```

Key files:
- `src/runtime/pipeline.py` — stage runner, transition logic
- `src/runtime/pipeline_context.py` — shared state flowing through stages
- `src/runtime/stages/` — one file per stage

### The Plan

A `Plan` (in `src/planning/schema.py`) is the core data structure:
- `original_query: str` — what the user asked
- `steps: list[Step]` — the work to do
- `requires_synthesis: bool` — whether to synthesize a response after execution
- `risk: str` — used to scale council review

A `Step` has:
- `description: str` — natural language instruction to the LLM
- `action_type: ActionType` — category (ANALYSIS, SHELL, FILE_IO, CONVERSATION, etc.)
- `tool: str | None` — if set, execution stage uses ONLY this tool
- `flags: StepFlags` — retry, escalate, defer policy (set at plan time)
- `status`, `result`, `error` — runtime state

### Workflows

In `src/workflows/implementations/`, each workflow is a class with:
- `name: str` — identifier
- `intent: str` — natural language description for the WorkflowSelector
- `pattern: re.Pattern` — regex for fallback matching
- `generate_plan(match, message) -> Plan` — produces a complete Plan object

The `WorkflowMatchStage` uses an LLM-based `WorkflowSelector` (path 1) or regex
(path 2) to pick a workflow and generate a plan. If no workflow matches, the
`PlanningStage` runs the full planner LLM to generate a plan.

Current workflows:
- `deep-disassembly` — recon → Ghidra → synthesis → write → verify
- `test-reconstruction` — diff → fix loop → verify
- `solve-crackme` — angr symbolic execution
- `audit-binary` — security analysis
- `function-map` — call graph
- `quick-recon` — fast triage
- `analyze-and-write`, `read-modify-write`, `hash-and-report` — generic patterns

### The Monitor

`ExecutionMonitor` (in `src/runtime/monitor.py`) is called after each step
completes. It uses heuristics first, then an LLM call if uncertain. Returns
a `StepDecision`: CONTINUE, RETRY, REPLAN, DEFER, SKIP, or ESCALATE.

The monitor only has visibility into the **current step's result**. It cannot
assess whether the overall plan goal was achieved.

### The Council

`CouncilStage` (in `src/runtime/stages/council.py`) runs an adversarial review
of the plan before execution. It uses multiple LLM "councillors" to challenge
steps. Plans generated by the WorkflowSelector bypass the council entirely via
the `_WORKFLOW_PATHS` set.

---

## The Catalogued Drift: What's Out of Place

These are specific, confirmed instances of infrastructure decisions living outside
the runtime infrastructure. Each entry includes the file and approximate location.

### DRIFT-1: Loop Control in Step Descriptions (CRITICAL)

**File:** `src/workflows/implementations/test_reconstruction.py` lines 85-86

The workflow step 3 description contains:
```
"  4. If still diverging: fix again and retest. Repeat up to 8 times.\n"
"  5. Stop only when all_match=true or after 8 iterations.\n"
```

Iteration count and stopping criteria are **infrastructure decisions**. They
belong in `ToolLoopConfig.max_iterations` or a future ContinuationStage loop
limit. Encoding them in a string the LLM reads is not reliable — the model can
ignore or misinterpret them. The infrastructure should enforce limits, not
describe them.

**Similar pattern in `deep_disassembly.py` step 9:** prescribes "BEFORE writing
any code, explicitly confirm..." — this is prompting strategy, not infrastructure
loop control, but reflects the same pattern of encoding policy in descriptions.

### DRIFT-2: Static Completion Evaluation (MAJOR)

**Files:**
- `src/planning/schema.py` line ~170: `requires_synthesis: bool = True`
- `src/runtime/stages/execution.py` lines ~141-143: checks `plan.requires_synthesis`
- `src/runtime/stages/synthesizer.py` lines ~45-46: skips if `not context.plan.requires_synthesis`

The question "should we synthesize a response?" is answered **at plan generation
time** — a static boolean set by the workflow or planner LLM before any tools
have run. This is backwards. Whether synthesis is appropriate depends on what
actually happened during execution. The infrastructure should evaluate this
dynamically, not accept a plan-time verdict.

Some workflows set `requires_synthesis=False` explicitly (e.g., `read_modify_write.py`
line ~51). This is workflow code controlling a synthesizer infrastructure decision.

### DRIFT-3: Plan Metadata Pre-codes Infrastructure Policy (MAJOR)

**File:** `src/planning/schema.py` `StepFlags` dataclass

```python
@dataclass
class StepFlags:
    retry: bool = False      # controls whether ExecutionStage retries
    escalate: bool = False   # controls whether ExecutionStage escalates to user
    defer: bool = False      # controls whether ExecutionStage defers
    retry_count: int = 0     # runtime state (execution-managed)
    deferred: bool = False   # runtime state (execution-managed)
    skipped: bool = False    # runtime state (execution-managed)
```

The `retry`, `escalate`, and `defer` booleans are set by the planner/workflow
at plan creation time. The ExecutionStage uses them to pre-configure step
execution strategy. This means the planner is making infrastructure decisions
(retry policy, escalation policy) before execution begins, with no knowledge
of what will actually happen.

Additionally, `Step.tool` (when non-None) pre-selects the tool ExecutionStage
will use, giving the execution stage zero agency in tool selection for that step.

The `Plan.risk` field controls council scaling (`dynamic_scaling` in config).
Risk is set by the routing classifier and flows through as plan metadata, making
council behavior contingent on a classifier output rather than infrastructure
assessment.

### DRIFT-4: Tool Availability Policy Is Hybrid (MODERATE)

**File:** `src/runtime/stages/execution.py` lines ~215-228

```python
if step.action_type == ActionType.CONVERSATION:
    tools = []
elif step.tool:                          # PLAN-TIME: use pre-selected tool only
    tools = self._registry.get_tool_schema(step.tool)
    utility_tools = _step_utility_tools(step)   # HEURISTIC: hardcoded extras
else:                                    # RUNTIME: router selects toolsets
    selected = self._router.select(step.description, ...)
    tools = self._registry.get_toolset_schema(selected)
```

The `_step_utility_tools()` function (lines ~74-81) has hardcoded rules:
- `write_file` always gets `make_directory`
- `bash_exec` always gets `read_file`

These are heuristic policies embedded in infrastructure code, not configurable
and not data-driven. The three-tier system (plan-time → heuristic → runtime)
is inconsistent and hard to reason about.

### DRIFT-5: Council Bypass Via Routing Metadata (MAJOR)

**File:** `src/runtime/stages/council.py` lines 26-29, 93-97

```python
_WORKFLOW_PATHS = {"classifier_hint", "classifier_hint_direct", "regex", "fallback", "selector"}

if context.routing_path in _WORKFLOW_PATHS:
    logger.info(f"  critic: skipped (workflow-generated plan via '{context.routing_path}')")
    return StageResult(status=StageStatus.OK, updated_context=context)
```

The CouncilStage reads `context.routing_path` (set by WorkflowMatchStage) to
decide whether to run. This is hidden coupling: the infrastructure's behavior
changes based on metadata from another stage, not based on its own assessment.
The bypass is also brittle — we had to add "selector" to the set manually when
WorkflowSelector was promoted to path 1.

A workflow plan that happens to have a dangerous step bypasses council review
entirely because of how it arrived, not because it's been evaluated as safe.

### DRIFT-6: Monitor Only Has Step-Level Visibility (MODERATE)

**File:** `src/runtime/monitor.py`

The `ExecutionMonitor.assess()` method evaluates whether a single step succeeded
and returns a `StepDecision`. It has no concept of:
- Whether the overall plan goal was achieved
- Whether the user's original question was answered
- Whether the execution produced the required artifacts

Plan-level completion is delegated entirely to the static `requires_synthesis`
boolean. The monitor — the infrastructure component best positioned to assess
ongoing progress — never asks "are we done with the task?"

### DRIFT-7: Workflow Plan Generation Is Too Complex (MAJOR)

**File:** `src/workflows/implementations/deep_disassembly.py`

The `generate_plan()` method is ~180 lines (lines 69-278) and makes these
decisions that should belong to infrastructure or runtime:

- Platform detection: `platform.system() == "Darwin"` chooses `otool` vs `objdump`
- Tool availability: `bool(settings.ghidra_home)` switches between Ghidra and
  manual disassembly plans — this is a runtime capability check
- Container availability: `ContainerSession.available()` changes step 11 structure
- Goal inference: `_infer_goal()` decides synthesis description based on message

Workflows are supposed to encode **domain knowledge** about how to approach a
task. They are not supposed to make runtime capability assessments or adapt their
structure to infrastructure availability. That's the infrastructure's job.

**File:** `src/workflows/implementations/test_reconstruction.py`

Step 3 description is ~200 words of prescriptive instructions including: loop
iteration counts, tool call sequencing, artifact recall instructions, and
diagnostic decision trees. A step description should say WHAT to do, not
encode the entire execution strategy.

### DRIFT-8: No Pipeline-Level Continuation (MODERATE)

**File:** `src/runtime/pipeline.py`

The pipeline is strictly linear. After `ExecutionStage` completes, the only
options are:
- `DONE` → return immediately
- `OK` → proceed to SynthesizerStage

There is no mechanism for the pipeline to say "execution is done but the task
is not — generate a new plan and continue." The pipeline has no concept of
task-level goals that span multiple plan executions.

Replanning (`REPLAN` from the monitor) only happens inside `ExecutionStage`'s
internal loop — it generates new steps for the remaining plan, not an entirely
new plan from scratch. The pipeline runner (`pipeline.py`) never replans.

### DRIFT-9: Synthesizer Makes No Decisions (MINOR)

**File:** `src/runtime/stages/synthesizer.py`

The SynthesizerStage runs when `plan.requires_synthesis` is True, calls
`provider.chat()`, and returns `DONE`. It has no ability to assess whether
the synthesized response actually addressed the user's goal. A synthesis that
says "I don't know" passes through identically to a correct synthesis.

The optional quality gate (`synthesis_quality` config) is advisory-only and
almost always disabled.

### DRIFT-10: Container Tool Makes Isolation Decisions (MODERATE)

**File:** `src/tools/implementations/container/tools.py` lines ~327-408

`DiffBehaviorTool.execute()` checks `adapter.runs_locally` (set as a class
attribute in `adapters.py`) to decide whether the oracle or candidate runs on
the host or in a Docker container. This is a sandboxing/isolation decision that
should be made by infrastructure policy (SandboxConfig), not hardcoded in an
adapter class.

---

## The Desired End State

### Runtime Infrastructure Owns All Control Flow

The pipeline is the only thing that decides:
- Whether execution continues, retries, or stops
- Whether the council reviews a plan
- Whether synthesis happens
- Whether a new plan is needed (continuation)
- What happens next in all cases

Runtime infrastructure is aware of state at everypoint through the pipeline.
- At any point in time during any stage, the infrastructure must know context, tokens, current tasks and overall goals
- Infrastructure should offer reproducible metrics at any point in any stage of a conversation through logs or output files
- Output should be usable data for visualizing all metrics of the system at any point, turn, stage, process, time during a conversation.

Plan metadata becomes purely **descriptive** (what to do) rather than
**prescriptive** (how the infrastructure should behave).

### Workflows Become Skills

Instead of workflows generating complete Plans that replace the planner, they
become **named building blocks** (skills) that the planner can invoke:

```
User: "decompile _tests/proc and iterate until proc_clone matches"
  → Planner generates: 
      Step 1: invoke skill "deep-disassembly" on _tests/proc
      Step 2: invoke skill "test-reconstruction" on _tests/proc vs proc_clone.c
      [ContinuationStage evaluates: all_match=true? → synthesize, else → loop]
```

This means:
- The planner orchestrates skills, it doesn't get replaced by them
- Skills are composable — the planner can chain them
- Complex compound requests go to the planner, not the WorkflowSelector
- Each skill is smaller and focused (not 180-line generate_plan methods)

### The ContinuationStage

A new stage inserted between ExecutionStage and SynthesizerStage:

```
ExecutionStage
  → ContinuationStage  ← NEW
  → SynthesizerStage
```

**ContinuationStage responsibilities:**
1. Assess whether the user's original goal was achieved (one focused LLM call)
2. If achieved → pass to SynthesizerStage (or return DONE if no synthesis needed)
3. If not achieved → identify the gap and generate a continuation plan
4. Execute the continuation plan (back through ExecutionStage)
5. Repeat until achieved or iteration limit reached
6. Manage iteration count (not encoded in step descriptions)
7. Pass relevant context forward (decompilation artifacts, diff results, etc.)

**Key design principles:**
- ContinuationStage is the ONLY place that asks "are we done with the task?"
- It replaces `requires_synthesis` as the completion evaluator
- It enables the fix loop we've been fighting — write → verify → if wrong → analyze → fix → verify, as many times as needed
- It has access to the full execution history to make informed continuation decisions
- It can invoke skills (former workflows) as continuation steps
- It can pass context forward: "here's what Ghidra found, use it in the next iteration"

---

## What You Need to Produce

Generate a complete set of plan files in `_plans/`. Use the next available
number after 0078 (check the directory for the actual next number).

The plans must cover:

### Plan Set A: Refactoring (address all catalogued drift)

**A.1 — Schema Cleanup**
- Remove infrastructure-controlling fields from `Plan` and `Step`
- Specifically: `requires_synthesis`, `StepFlags.retry/escalate/defer` as prescriptive
- Define what plan metadata SHOULD contain (descriptive only)
- Define what moves to config or infrastructure

**A.2 — Tool Selection Unification**  
- Eliminate the three-tier tool selection (plan-time → heuristic → runtime)
- Define a single consistent policy for how tools are selected per step
- Move `_step_utility_tools()` hardcoded rules to config or data-driven policy
- Clarify the role of `step.action_type` vs `step.tool` vs router

**A.3 — Council Bypass Refactor**
- Remove `_WORKFLOW_PATHS` hardcoded set from `council.py`
- Define explicit, principled criteria for when the council runs
- Consider: plan complexity score, risk level, source of plan (planner vs skill)

**A.4 — Workflow Thinning (Skill Extraction)**
- Reduce `generate_plan()` methods to pure domain knowledge
- Remove platform/capability detection from workflows (move to infrastructure)
- Remove iteration counts and loop logic from step descriptions
- Define the "skill" interface: what a skill is, how it's invoked, what it returns
- Show how existing workflows are refactored into skills

**A.5 — Monitor Enhancement**
- Add plan-level goal assessment to ExecutionMonitor
- Define "goal criteria" concept: how does the monitor know what "done" means?
- Add a "goal achieved" signal to StepDecision options

### Plan Set B: ContinuationStage Design and Implementation

**B.1 — ContinuationStage Architecture**
- Full data model: inputs, outputs, state
- Decision logic: how it evaluates completion (LLM call design)
- Iteration management: how it counts, caps, and tracks continuation cycles
- Context propagation: how it passes artifacts and analysis forward
- Integration with the skill system: how it invokes skills as continuation steps

**B.2 — ContinuationStage Implementation**
- New file: `src/runtime/stages/continuation.py`
- `PipelineContext` additions needed
- `Config` additions needed
- Wire into `src/agent.py` pipeline construction

**B.3 — Completion Criteria System**
- How skills/workflows declare what "done" means
- How ContinuationStage evaluates completion against those criteria
- Built-in criteria for common patterns (diff_behavior all_match, file written, etc.)

**B.4 — Fix Loop Migration**
- Migrate `test-reconstruction` from a workflow to a skill
- The fix loop (write → verify → analyze → fix → verify) becomes a ContinuationStage
  behavior, not a step description
- Show the before/after for this specific case as a worked example

---

## Important Constraints for Your Plans

1. **Read the code before writing plans.** Reference actual line numbers and
   actual code. Do not generalize.

2. **Preserve what works.** The routing system, the tool ecosystem, the
   council deliberation, the artifact store, the context manager — these are
   working and shouldn't be touched unless directly implicated in drift.

3. **Incremental.** Plans should be executable in order. Each phase should
   leave the system in a working state.

4. **The naming convention for plan files:**
   - Design documents: `NNNN-topic.md`
   - Phase documents: `NNNNa-topic.md`, `NNNNb-topic.md`, etc.

5. **Be specific about what changes and what doesn't.** "Refactor the schema"
   is not a plan. "Remove `requires_synthesis` from Plan dataclass, update
   the 3 places that read it (execution.py:142, synthesizer.py:45, schema.py:170),
   replace with ContinuationStage evaluation" is a plan.

6. **The runtime infrastructure is the universe.** Every plan decision should
   strengthen this property, not weaken it.
