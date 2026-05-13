# 0079c — Phase C: Skills System, WorkflowSelector Demotion, SkillExpansionStage

> **Read first:** `_plans/0079-runtime-as-god.md` §0, §1.2, §4.
> Largest phase in the initiative. Allocate the most time here.
> Phases 0079a and 0079b should be merged before starting this one.

## Goal

Refactor the workflow system so that:

1. **Workflows are renamed to "skills"** — passive building blocks that
   declare what they do but contain no infrastructure logic
   (no platform detection, no capability checks, no loop counts in
   step descriptions).
2. **`WorkflowMatchStage` is demoted to advisory.** It no longer produces
   `Plan`s. It at most writes a `skill_hint: str | None` into context
   for the planner to consider. The planner is the sole plan author.
3. **A new `SkillExpansionStage`** runs between `PlanningStage` and
   `EntityCriticStage`. It walks the plan; any step whose `tool` is of
   the form `skill:<name>` is replaced by the steps that skill emits.
   Re-numbers downstream steps. Idempotent on plans without skill calls.
4. **Existing workflow files are slimmed** to skill files: keep the
   intent string, keep domain knowledge in step descriptions, **remove**
   platform detection (`platform.system()`), capability detection
   (`settings.ghidra_home`, `ContainerSession.available()`), and any
   loop-iteration prescriptions.

This addresses **DRIFT-7** (workflow `generate_plan` overreach) and
**most of DRIFT-1** (loop control in descriptions). The remaining
`test_reconstruction.py` iteration count is removed in phase **0079i**.

## What changes structurally

| Component | Before | After |
|-----------|--------|-------|
| `src/workflows/` | 9 workflow classes producing full Plans | renamed `src/skills/`; classes emit step lists via `expand()` |
| `WorkflowMatcher` | walks workflows, returns Plan | becomes `SkillRegistry`; returns skill *names* and intents |
| `WorkflowSelector` | LLM picks one of 9 workflows, returns name | unchanged class, but result is now an **advisory** `skill_hint` for the planner |
| `WorkflowMatchStage` | sets `context.plan` | renamed `SkillHintStage`; sets `context.skill_hint` only — never `context.plan` |
| `PlanningStage` | runs only when `context.plan is None` | runs always (for plan-mode); takes `context.skill_hint` as suggestion in prompt |
| `SkillExpansionStage` | n/a | NEW — expands `skill:<name>` step references into concrete steps |
| Planner prompt | no skill awareness | learns about skills, can emit `tool="skill:<name>"` |

## Detailed changes

### Change 1 — Define the Skill interface

**New file:** `src/skills/base.py`

```python
"""Skill base class.

A skill is a named, passive building block. It knows how to expand a
sub-goal into concrete steps; it does NOT make runtime decisions.

Forbidden in skill code:
  - platform.system() and any host-specific branching
  - any read of `app_config.settings` for capability detection
    (e.g., `settings.ghidra_home`, `ContainerSession.available()`)
  - iteration counts or "repeat up to N times" in step descriptions
  - flag prescriptions on steps (retry/escalate/defer)

Skills declare WHAT they do. Infrastructure decides HOW.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from planning.schema import Step


@dataclass
class SkillContext:
    """Inputs available when expanding a skill into concrete steps.

    Populated by SkillExpansionStage from the parent plan and pipeline
    context. Skills read this to parameterize their step list — but
    must not branch on capabilities or platform.
    """
    original_query: str
    skill_args: dict      # arbitrary args the planner provided in step.description / a parsed payload
    starting_step_number: int


class Skill(ABC):
    """Base class for skills."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier; used as `tool="skill:<name>"`."""
        ...

    @property
    @abstractmethod
    def intent(self) -> str:
        """One-paragraph description for both the planner system prompt
        and (legacy) WorkflowSelector. Written for an LLM audience."""
        ...

    @abstractmethod
    def expand(self, ctx: SkillContext) -> list[Step]:
        """Return the concrete steps this skill contributes.

        Steps must be numbered starting from ctx.starting_step_number.
        Steps must NOT contain iteration counts, capability checks,
        or runtime flag prescriptions in their descriptions.
        """
        ...

    @property
    def completion_criteria(self) -> "CompletionCriteria | None":
        """Optional — populated in phase 0079g.
        Skills that have a clear notion of done can declare it here.
        Default None means ContinuationStage uses LLM judgment alone.
        """
        return None
```

`CompletionCriteria` is forward-declared as a string in the typing
import — phase **0079g** introduces the actual class.

### Change 2 — Skill registry

**New file:** `src/skills/registry.py`

```python
"""Skill registry — replaces workflows.matcher.WorkflowMatcher."""
from __future__ import annotations
from skills.base import Skill
from skills.implementations import ALL_SKILLS
from logger import get_logger

logger = get_logger(__name__)


class SkillRegistry:

    def __init__(self) -> None:
        self._by_name = {s.name: s for s in ALL_SKILLS}

    def get(self, name: str) -> Skill | None:
        return self._by_name.get(name)

    def names(self) -> list[str]:
        return list(self._by_name.keys())

    def descriptions(self) -> list[tuple[str, str]]:
        return [(s.name, s.intent) for s in ALL_SKILLS]
```

`workflows/matcher.py` is **deleted** at the end of this phase.

### Change 3 — Migrate existing workflows to skills

For each file in `src/workflows/implementations/`, create a corresponding
file in `src/skills/implementations/` and slim it. The brief gives a
worked example for `test_reconstruction` in phase 0079i; here we
specify the rules and apply them to all current workflows.

**Slimming rules (apply uniformly):**

- Class inherits from `Skill` (not `Workflow`).
- Replace `generate_plan(match, message) -> Plan` with
  `expand(ctx: SkillContext) -> list[Step]`.
- **Delete** all calls to `platform.system()` and any branching on
  `is_macos`. The OS-specific tool selection (e.g., `otool` vs
  `objdump`) becomes the planner's job — a `bash_exec` step with
  description "dump disassembly" lets the router and underlying tool
  pick the right binary, or a future tool registry capability. For
  this phase, simply **do not branch** — write the description as
  generic ("dump the disassembly of <target>") and let the LLM
  agent pick the command.
- **Delete** `bool(settings.ghidra_home)` branching. If a skill needs
  Ghidra, declare it in the intent ("requires ghidra"); execution-time
  the tool either succeeds or fails, and the runtime monitor handles
  failure. In other words: capability detection moves *out of plan
  generation* and into *runtime tool execution*. If a skill can be
  realistically split into "ghidra path" and "no-ghidra path", make
  them **two skills** (e.g., `deep-disassembly` and `quick-disassembly`).
  Practically — keep the existing `ghidra_*` tool calls in
  `deep-disassembly` since that's the skill's domain; remove the
  fallback to bash-otool inside the same skill. Add a sibling skill
  `manual-disassembly` if the bash path is needed.
- **Delete** any `ContainerSession.available()` branching. Same
  principle: a skill is for one approach. Container-required steps
  emit `diff_behavior` calls; the tool itself raises a clear error if
  the container is unavailable.
- **Delete** iteration counts ("Repeat up to 8 times", "Stop after N
  iterations"). Loop control is ContinuationStage's job (phase 0079g).
- **Delete** `flags=StepFlags(...)` arguments. Steps don't need flags;
  in phase 0079f the prescriptive booleans go away and `Step.flags`
  becomes runtime-state-only with a default factory.
- The `intent` and (optional) `pattern` properties are kept. The
  pattern is no longer load-bearing for plan generation but `SkillHintStage`
  may still use it as a cheap regex hint.

**Specific files to migrate (one per existing workflow):**

| Source | Destination | Notes |
|--------|-------------|-------|
| `src/workflows/implementations/deep_disassembly.py` (417 lines) | `src/skills/implementations/deep_disassembly.py` | Drop `is_macos`, drop `ghidra_available` branching (keep ghidra path only — see below), drop `ContainerSession.available()`, drop the giant `BEFORE writing any code, explicitly confirm` block from `_infer_goal` (move that prompting strategy into the planner system prompt or into the synthesizer system prompt). Keep `_extract_target` and `_infer_goal` (slimmed). |
| `src/workflows/implementations/test_reconstruction.py` | `src/skills/implementations/test_reconstruction.py` | Drop the 8-iteration text in step 3. Step 3's description becomes "Read the DiffReport from step 2. Identify and fix the bugs in the candidate source. Call write_file with the corrected source." Phase 0079i adds the loop via ContinuationStage. |
| `src/workflows/implementations/solve_crackme.py` | `src/skills/implementations/solve_crackme.py` | Inspect for any infrastructure leakage; otherwise straight rename. |
| `src/workflows/implementations/audit_binary.py` | `src/skills/implementations/audit_binary.py` | Straight rename if clean. |
| `src/workflows/implementations/function_map.py` | `src/skills/implementations/function_map.py` | Straight rename. |
| `src/workflows/implementations/quick_recon.py` | `src/skills/implementations/quick_recon.py` | Straight rename. |
| `src/workflows/implementations/analyze_and_write.py` | `src/skills/implementations/analyze_and_write.py` | Straight rename. |
| `src/workflows/implementations/read_modify_write.py` | `src/skills/implementations/read_modify_write.py` | **Delete** `requires_synthesis=False` (the skill no longer creates a Plan; phase 0079f deletes the field anyway). |
| `src/workflows/implementations/hash_and_report.py` | `src/skills/implementations/hash_and_report.py` | Straight rename. |

For the `deep-disassembly` migration specifically: the current file
has two branches — Ghidra-available and not. Per the rules, keep the
Ghidra path in `deep_disassembly.py` and drop the fallback. If the
non-Ghidra path is genuinely valuable, create a separate
`manual_disassembly.py` skill. **Recommendation:** in phase 0079c,
keep only the Ghidra path; defer creation of `manual_disassembly` to
a follow-up unless the user requests it.

**Aggregate file:** `src/skills/implementations/__init__.py`

```python
from skills.base import Skill
from skills.implementations.deep_disassembly import DeepDisassembly
from skills.implementations.test_reconstruction import TestReconstruction
from skills.implementations.solve_crackme import SolveCrackme
from skills.implementations.audit_binary import AuditBinary
from skills.implementations.function_map import FunctionMap
from skills.implementations.quick_recon import QuickRecon
from skills.implementations.analyze_and_write import AnalyzeAndWrite
from skills.implementations.read_modify_write import ReadModifyWrite
from skills.implementations.hash_and_report import HashAndReport

ALL_SKILLS: list[Skill] = [
    SolveCrackme(),
    AuditBinary(),
    TestReconstruction(),
    DeepDisassembly(),
    FunctionMap(),
    QuickRecon(),
    AnalyzeAndWrite(),
    ReadModifyWrite(),
    HashAndReport(),
]
```

After all skills migrate and tests pass, **delete** `src/workflows/`
entirely. Update all imports across the repo (see Change 7).

### Change 4 — Demote `WorkflowMatchStage` to `SkillHintStage`

**File rename:** `src/runtime/stages/workflow_match.py` → `src/runtime/stages/skill_hint.py`

The stage no longer sets `context.plan`. It only consults the
`WorkflowSelector` (now a "skill suggester") and the regex patterns,
and writes the result into `context.skill_hint`.

**New body of the stage:**

```python
"""SkillHintStage — advisory skill suggester.

Runs a cheap LLM (or regex) pass to suggest which skill, if any, the
planner might want to invoke. The output is HINT ONLY: it is not
load-bearing. The planner is free to ignore it.
"""
from __future__ import annotations
from runtime.classifier import WorkflowSelector  # rename in a follow-up; class behavior unchanged
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from skills.registry import SkillRegistry
from logger import get_logger

logger = get_logger(__name__)


class SkillHintStage(Stage):
    """Suggests a skill name to the planner. Never produces a plan."""

    name = "SkillHintStage"

    def __init__(
        self,
        skill_registry: SkillRegistry,
        skill_selector: WorkflowSelector,   # class can keep its old name; behavior is unchanged
        spinner,
    ) -> None:
        self._registry = skill_registry
        self._selector = skill_selector
        self._spinner = spinner

    def run(self, context: PipelineContext) -> StageResult:
        if context.classification is None or context.classification.mode != "plan":
            return StageResult(status=StageStatus.OK, updated_context=context)

        logger.info(banner("Skill hint"))
        descriptions = self._registry.descriptions()
        self._spinner.update("Routing...")
        chosen = self._selector.select(context.user_message, descriptions)
        if chosen and self._registry.get(chosen) is not None:
            logger.info(f"  hint: '{chosen}' (advisory; planner may override)")
            context.skill_hint = chosen
        else:
            logger.info("  hint: none")
            context.skill_hint = None

        return StageResult(status=StageStatus.OK, updated_context=context)
```

**Important:** this stage **does not** set `context.plan`. The classifier
hint path and regex path are gone; only the LLM selector is used (it's
already an LLM call we make once per plan-mode query — same cost
profile as before).

### Change 5 — Add `skill_hint` field to `PipelineContext`

**File:** `src/runtime/pipeline_context.py`

After the existing `# ── Set by WorkflowMatchStage ─` block (lines ~38-43),
replace with:

```python
# ── Set by SkillHintStage (advisory only) ────────────────────────
# Name of a skill the planner is hinted to use. Planner reads this
# in its system prompt; planner may ignore it. NOT load-bearing.
skill_hint: str | None = None

# Legacy fields — retained as informational only, NEVER as policy.
# These are no longer read by any stage to switch behavior.
# Remove in a future cleanup once all references are gone.
routing_path: str | None = None
workflow_name: str | None = None
```

### Change 6 — Planner learns about skills

**File:** `src/planning/planner.py`

The planner system prompt currently lists individual tools. Augment it
with the available skill names so the LLM may emit
`tool="skill:<name>"` for a step.

In `Planner.plan` (around line 39), build a "skills available" block
similar to `build_tool_list(ALL_TOOLSETS)`. Inject the available skill
names + intents into the system prompt. The planner must understand
that a skill step's `tool` field carries the skill name as
`skill:<skill-name>`.

**Specific edits:**

1. Add a helper to `src/planning/prompts.py`:

   ```python
   def build_skill_list(skills: list[tuple[str, str]]) -> str:
       """Format (name, intent) pairs for the planner system prompt."""
       lines = ["Skills (invoke as tool='skill:<name>' on a step):"]
       for name, intent in skills:
           lines.append(f"  - skill:{name} — {intent}")
       return "\n".join(lines)
   ```

2. Extend `PLANNING_SYSTEM_PROMPT` to include `{skill_list}` placeholder.
   Add a paragraph: "When a registered skill matches a sub-goal, prefer
   invoking it as `tool='skill:<name>'`. Skills expand into concrete
   steps at runtime."

3. In `Planner.plan` (and `Planner.revise`, `Planner.replan`):

   ```python
   from skills.registry import SkillRegistry
   # Inject through __init__ or read from a module-level singleton — pick
   # whichever matches existing dependency-injection style in agent.py.
   skill_descriptions = self._skill_registry.descriptions()
   system = PLANNING_SYSTEM_PROMPT.format(
       max_steps=config.planning.max_steps,
       tool_list=build_tool_list(ALL_TOOLSETS),
       skill_list=build_skill_list(skill_descriptions),
   )
   ```

4. When the planner has a `skill_hint`, append a soft instruction:

   ```python
   user_turn = PLANNING_USER_TURN.format(...)
   if skill_hint is not None:
       user_turn += (
           f"\n\nHint: a previous classifier suggested skill:{skill_hint} "
           f"may be relevant. Use it if and only if it actually fits."
       )
   ```

   Pass `skill_hint=context.skill_hint` from `PlanningStage.run` into
   `Planner.plan`.

5. **Delete** the no-op short-circuit in `PlanningStage` at
   `src/runtime/stages/planning.py:51-52`:

   ```python
   # No-op if a workflow already produced a plan.
   if context.plan is not None:
       return StageResult(status=StageStatus.OK, updated_context=context)
   ```

   The planner is the sole plan author now. Plan should always be None
   when entering this stage.

### Change 7 — Add `SkillExpansionStage`

**New file:** `src/runtime/stages/skill_expansion.py`

```python
"""SkillExpansionStage — expands skill:<name> steps into concrete steps.

Runs after PlanningStage, before EntityCriticStage. Idempotent on plans
without skill calls. Re-numbers steps after expansion.
"""
from __future__ import annotations
from planning.schema import Plan, Step
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from skills.base import SkillContext
from skills.registry import SkillRegistry
from logger import get_logger

logger = get_logger(__name__)

_SKILL_PREFIX = "skill:"


class SkillExpansionStage(Stage):
    """Inlines skill calls into concrete plan steps.

    A step with tool='skill:foo' is replaced by the steps that foo.expand()
    returns, with step numbers continuous within the parent plan.
    """

    name = "SkillExpansionStage"

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def run(self, context: PipelineContext) -> StageResult:
        plan = context.plan
        if plan is None:
            return StageResult(status=StageStatus.OK, updated_context=context)

        # Fast path: no skill calls present
        if not any(
            (s.tool or "").startswith(_SKILL_PREFIX) for s in plan.steps
        ):
            return StageResult(status=StageStatus.OK, updated_context=context)

        logger.info(banner("Skill expansion"))

        new_steps: list[Step] = []
        for s in plan.steps:
            tool = s.tool or ""
            if not tool.startswith(_SKILL_PREFIX):
                new_steps.append(s)
                continue

            skill_name = tool[len(_SKILL_PREFIX):]
            skill = self._registry.get(skill_name)
            if skill is None:
                # Unknown skill — keep the step as a literal so the runtime
                # surfaces a useful error, but log it.
                logger.info(f"  unknown skill '{skill_name}' — keeping step as literal")
                new_steps.append(s)
                continue

            ctx = SkillContext(
                original_query=plan.original_query,
                skill_args={"description": s.description},
                starting_step_number=len(new_steps) + 1,
            )
            try:
                expanded = skill.expand(ctx)
            except Exception as e:
                logger.info(f"  skill '{skill_name}' expand failed: {e!r}")
                new_steps.append(s)
                continue
            logger.info(
                f"  step {s.step}: skill:{skill_name} → {len(expanded)} concrete step(s)"
            )
            new_steps.extend(expanded)

        # Re-number sequentially
        for i, st in enumerate(new_steps, 1):
            st.step = i

        plan.steps = new_steps
        context.plan = plan
        logger.info(f"  expanded plan: {len(new_steps)} step(s)")
        return StageResult(status=StageStatus.OK, updated_context=context)
```

### Change 8 — Wire stages into pipeline

**File:** `src/agent.py`

In `_build_pipeline` (lines 36-101):

1. Replace the `WorkflowMatchStage` import and instantiation with
   `SkillHintStage` and the new dependency `SkillRegistry`.
2. Insert `SkillExpansionStage` between `PlanningStage` and
   `EntityCriticStage`.
3. Replace `self.workflow_matcher = WorkflowMatcher()` with
   `self.skill_registry = SkillRegistry()`.
4. Update all references from `workflow_matcher` to `skill_registry`.
5. The `RoutingStage` constructor takes `workflow_matcher` for the
   purpose of injecting workflow descriptions into the routing system
   prompt — change that param to `skill_registry` and have the stage
   call `skill_registry.descriptions()` instead.

Final stage list:

```python
stages = [
    RoutingStage(provider=p.provider, context_mgr=p.context_mgr,
                 skill_registry=p.skill_registry, messenger=p.messenger),
    DirectInlineStage(messenger=p.messenger),
    SkillHintStage(skill_registry=p.skill_registry,
                   skill_selector=p.workflow_selector, spinner=p.spinner),
    PlanningStage(planner=p.planner, validator=p.validator, spinner=p.spinner),
    SkillExpansionStage(registry=p.skill_registry),
    EntityCriticStage(entity_critic=p.entity_critic),
    ValidatorStage(),
    CouncilStage(critic=p.critic, planner=p.planner,
                 validator=p.validator, spinner=p.spinner),
    ExecutionStage(...),         # unchanged
    SynthesizerStage(synthesizer=p.synthesizer, spinner=p.spinner),
    direct_execution,
]
```

### Change 9 — Update `RoutingStage` to consume skill descriptions

**File:** `src/runtime/stages/routing.py`

Currently uses `self._workflow_matcher.get_descriptions()` (line 57)
and threads `valid_wf_names` into the routing parser (line 58).
Rename to `self._skill_registry.descriptions()` and `valid_skill_names`.
The `workflow_hint` field on `ClassifierResult` becomes `skill_hint` —
update `runtime/schema.py:12` and `runtime/utils.parse_routing_response`
accordingly.

```python
# src/runtime/schema.py — ClassifierResult
@dataclass
class ClassifierResult:
    mode: str            # "plan" | "direct"
    risk: str            # "low" | "moderate" | "high"
    skill_hint: str | None = None    # was workflow_hint
```

Search every reader of `workflow_hint` and rename:

```bash
rg -n "workflow_hint" src/
```

### Change 10 — Sweep references and delete `src/workflows/`

After all other changes:

```bash
rg -n "workflows\." src/        # imports — should now point to skills/
rg -n "WorkflowMatcher" src/    # zero hits
rg -n "WorkflowMatchStage" src/ # zero hits
rg -n "workflow_matcher" src/   # zero hits
rg -n "_WORKFLOW_PATHS" src/    # already zero from 0079b
```

Delete `src/workflows/` directory entirely after confirming the above.

`WorkflowSelector` (`src/runtime/classifier.py`) is renamed
`SkillSelector` for hygiene. Class behavior is unchanged. Update
the lone import in `agent.py`.

## Files (full list)

**New files:**
- `src/skills/__init__.py`
- `src/skills/base.py`
- `src/skills/registry.py`
- `src/skills/implementations/__init__.py`
- `src/skills/implementations/<one per existing workflow>.py` (9 files)
- `src/runtime/stages/skill_expansion.py`

**Renamed files:**
- `src/runtime/stages/workflow_match.py` → `src/runtime/stages/skill_hint.py`
- `src/runtime/classifier.py` (class rename inside, file stays — the file already mixes things)

**Modified files:**
- `src/runtime/pipeline_context.py` — replace `workflow_name`/`routing_path` block with `skill_hint` (keep legacy fields commented as informational)
- `src/runtime/schema.py` — `workflow_hint` → `skill_hint` on `ClassifierResult`
- `src/runtime/utils.py` — `parse_routing_response` returns `skill_hint`
- `src/runtime/stages/routing.py` — consume skill registry, propagate skill_hint
- `src/runtime/stages/planning.py` — delete the workflow-already-set short-circuit; receive `skill_hint` from context
- `src/planning/planner.py` — add `skill_registry` dep; inject skills into system prompt; thread `skill_hint`
- `src/planning/prompts.py` — add `build_skill_list` and update template
- `src/agent.py` — pipeline assembly with new stages

**Deleted files (after sweep):**
- `src/workflows/` (entire directory)

## Verification

```bash
pytest -x -q

# Imports clean — no leftover workflow refs
rg -n "from workflows" src/    # should be empty
rg -n "WorkflowMatcher" src/   # should be empty

# Plan-mode end-to-end:
# 1. Trivial plan-mode request that does NOT match any skill →
#    SkillHintStage logs "hint: none"; PlanningStage runs;
#    SkillExpansionStage fast-paths.
python -m src.main <<< "list the files in /tmp"

# 2. Skill-matching request →
#    SkillHintStage logs the hint;
#    PlanningStage emits a step with tool="skill:<name>";
#    SkillExpansionStage logs "step N: skill:foo → M concrete step(s)";
#    Plan executes.
python -m src.main <<< "decompile _tests/proc with deep disassembly"

# 3. Compound request → planner emits two skill steps that both expand.
```

## Done when

- [ ] `src/workflows/` is deleted.
- [ ] `src/skills/` exists with 9 skill implementations, all of which
      pass the slimming rules (no `platform.system`, no `settings.X`
      capability checks, no iteration counts, no `flags=StepFlags(...)`).
- [ ] `WorkflowMatchStage` is gone; `SkillHintStage` exists and only
      writes `context.skill_hint`.
- [ ] `SkillExpansionStage` exists and is wired between Planning and
      EntityCritic.
- [ ] Planner system prompt lists available skills.
- [ ] `routing_path` and `workflow_name` are no longer read by any
      stage to switch behavior. They may remain as logging-only fields
      or be removed; either is acceptable.
- [ ] `pytest` green.
- [ ] End-to-end skill flow works (deep-disassembly query produces a
      plan with `skill:deep-disassembly`, expanded into ~9 concrete
      steps, executed normally).

## Out of scope

- The exact iteration count for `test-reconstruction` (8 → ContinuationStage
  config). Phase **0079i** handles this; for now the step description
  drops the iteration count and ContinuationStage doesn't yet exist
  to put it back. Until phase 0079i lands, that one skill regresses on
  multi-iteration fix loops; this is acceptable per §5 invariant 3 of
  the design doc.
- `CompletionCriteria` field on `Skill`. Phase **0079g** introduces it.
- Removing `routing_path`/`workflow_name` from `PipelineContext`
  entirely. They're already neutralized; final removal is a follow-up.
