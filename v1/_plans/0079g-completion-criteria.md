# 0079g — Phase G: Completion Criteria System

> **Read first:** `_plans/0079d-continuation-arch.md` §2, §4, §7.
> **Depends on:** 0079c (Skill class), 0079e (ContinuationStage),
> 0079f (clean Plan/Step schema).

## Goal

Give skills a way to **declare what "done" means**, and give
ContinuationStage a way to **evaluate that declaration** without
always falling back to an LLM call.

This addresses **B.3** from the brief and replaces the last vestige of
DRIFT-2 (static `requires_synthesis`) with a dynamic, structured signal.

## Concept

Each skill optionally exposes a `CompletionCriteria` describing the
shape of "done." Two flavors:

1. **Structural** — a small predicate over plan-execution state.
   Example: "the last `diff_behavior` step's JSON result has
   `all_match=true`."
2. **LLM-judged** — a free-form prompt template that gets a yes/no
   answer from a single cheap LLM call. Example: "Did the agent fix
   the bug described in the user's request?"

ContinuationStage evaluates structural first (cheap), falls back to
LLM-judged, then falls back to the generic LLM judge from phase 0079e.

The skill also declares **what to do when criteria are met or not**:

```python
on_met:     ContinuationDecision.SYNTHESIZE | ContinuationDecision.DONE
on_unmet:   ContinuationDecision.LOOP        # implicit; no need to declare
```

## Files

**New:**
- `src/skills/criteria.py` — `CompletionCriteria` types and helpers

**Modified:**
- `src/skills/base.py` — add `completion_criteria` property
- `src/skills/implementations/test_reconstruction.py` — declare a structural criterion
- `src/skills/implementations/read_modify_write.py` — declare `on_met=DONE`
  (replaces the old `requires_synthesis=False`)
- `src/skills/implementations/deep_disassembly.py` — declare LLM-judged criterion
- `src/runtime/stages/continuation.py` — call into criteria evaluator
  before/instead of the generic LLM judge
- `src/runtime/stages/continuation.py` — implement `_build_continuation_plan`
  skill-replay tier

Other skills can leave `completion_criteria` as `None` (the default);
they fall through to the LLM judge.

## Detailed changes

### Change 1 — Define criteria types

**New file:** `src/skills/criteria.py`

```python
"""CompletionCriteria — how a skill declares 'done'.

ContinuationStage evaluates these. Structural criteria are pure
predicates over plan execution state and are cheap. LLM-judged criteria
make one focused chat call.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Any

from planning.schema import Plan, Step
from runtime.schema import ContinuationDecision


class CriteriaOutcome(str, Enum):
    MET           = "met"
    NOT_MET       = "not_met"
    INCONCLUSIVE  = "inconclusive"     # eval couldn't determine — fall through to LLM judge


@dataclass
class CriteriaContext:
    plan: Plan
    user_message: str


class CompletionCriteria(ABC):
    """Base class. on_met says what to do when the criteria are satisfied."""
    on_met: ContinuationDecision = ContinuationDecision.SYNTHESIZE

    @abstractmethod
    def evaluate(self, ctx: CriteriaContext) -> CriteriaOutcome:
        ...


# ── Structural criterion: predicate over the last step matching a tool ────

@dataclass
class StructuralCriteria(CompletionCriteria):
    """Pass if the last step using `tool_name` satisfies `predicate`.

    `predicate` receives the step's `result` string and returns True/False/None.
    None ⇒ inconclusive (e.g., couldn't parse JSON).
    """
    tool_name: str
    predicate: Callable[[str], bool | None]
    on_met: ContinuationDecision = ContinuationDecision.SYNTHESIZE

    def evaluate(self, ctx: CriteriaContext) -> CriteriaOutcome:
        target = next(
            (s for s in reversed(ctx.plan.steps) if s.tool == self.tool_name),
            None,
        )
        if target is None or not target.result:
            return CriteriaOutcome.INCONCLUSIVE
        verdict = self.predicate(target.result)
        if verdict is None:
            return CriteriaOutcome.INCONCLUSIVE
        return CriteriaOutcome.MET if verdict else CriteriaOutcome.NOT_MET


# ── LLM-judged criterion: prompt-driven yes/no ───────────────────────────

@dataclass
class LLMJudgedCriteria(CompletionCriteria):
    """The skill provides a focused yes/no prompt; ContinuationStage runs it.

    The judge prompt is appended to a system prompt that asks for strict
    JSON: {"satisfied": bool, "reason": "..."}.
    """
    prompt: str
    on_met: ContinuationDecision = ContinuationDecision.SYNTHESIZE
    # Evaluation is performed by ContinuationStage (it has the provider).
    # This class just carries the prompt; the stage handles I/O.

    def evaluate(self, ctx: CriteriaContext) -> CriteriaOutcome:
        # Sentinel — actual evaluation is done by ContinuationStage which
        # has access to the provider. The stage detects this type and
        # routes accordingly.
        return CriteriaOutcome.INCONCLUSIVE


# ── Helpers for common predicates ─────────────────────────────────────────

def diff_behavior_all_match(result: str) -> bool | None:
    """Predicate for diff_behavior: True iff DiffReport.all_match is True."""
    import json
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        # Some tool results wrap JSON in prose; do a simple search fallback.
        if '"all_match"' in result:
            if '"all_match": true' in result.lower():
                return True
            if '"all_match": false' in result.lower():
                return False
        return None
    if isinstance(data, dict):
        return bool(data.get("all_match", False)) if "all_match" in data else None
    return None


def file_written(path: str) -> Callable[[str], bool | None]:
    """Predicate factory: True iff a write_file step succeeded for `path`."""
    def predicate(result: str) -> bool | None:
        if not result:
            return None
        # write_file results typically include the path; treat absence of
        # error indicators as success.
        lowered = result.lower()
        if "error" in lowered or "permission denied" in lowered:
            return False
        return path in result or "wrote" in lowered
    return predicate
```

### Change 2 — Skill base extension

**File:** `src/skills/base.py`

Replace the placeholder `completion_criteria` property:

```python
from skills.criteria import CompletionCriteria

class Skill(ABC):
    # ... existing name / intent / expand ...

    @property
    def completion_criteria(self) -> CompletionCriteria | None:
        """Optional declaration of 'done'. Default None ⇒ LLM judge."""
        return None

    def continuation_steps(
        self,
        ctx: SkillContext,
        prior_results: list[Step],
    ) -> list[Step] | None:
        """Optional skill-replay for ContinuationStage LOOP path.

        Return new steps to execute the *next* iteration of this skill,
        or None if this skill is not loopable / cannot continue.
        Default: not loopable.
        """
        return None
```

### Change 3 — Declare criteria for selected skills

**File:** `src/skills/implementations/test_reconstruction.py`

Add the criterion at class body:

```python
from skills.base import Skill, SkillContext
from skills.criteria import (
    CompletionCriteria, StructuralCriteria, diff_behavior_all_match,
)
from runtime.schema import ContinuationDecision

class TestReconstruction(Skill):
    name = "test-reconstruction"
    intent = "..."  # unchanged

    @property
    def completion_criteria(self) -> CompletionCriteria | None:
        return StructuralCriteria(
            tool_name="diff_behavior",
            predicate=diff_behavior_all_match,
            on_met=ContinuationDecision.SYNTHESIZE,
        )

    def expand(self, ctx: SkillContext) -> list[Step]:
        # ... step list — see phase 0079i for the slimmed version ...

    def continuation_steps(self, ctx, prior_results):
        # The fix loop: read DiffReport, fix candidate, re-run diff_behavior.
        # Detailed in phase 0079i.
        ...
```

**File:** `src/skills/implementations/read_modify_write.py`

```python
from skills.criteria import StructuralCriteria, file_written
from runtime.schema import ContinuationDecision

class ReadModifyWrite(Skill):
    @property
    def completion_criteria(self):
        # Done when the output file was written. No synthesis needed —
        # the action is the answer (replaces old requires_synthesis=False).
        return StructuralCriteria(
            tool_name="write_file",
            predicate=file_written(self._output_path),     # set during expand
            on_met=ContinuationDecision.DONE,
        )
```

The output path needs to be available to the criteria — store it on
`self._output_path` during `expand()` (acceptable since each `Skill`
instance is shared; if instances are not safe to mutate, instead pass
the path via skill_args and store on a per-call frozen dataclass —
but the existing skills are stateless modules, so module-instance
attribute is OK).

**File:** `src/skills/implementations/deep_disassembly.py`

```python
from skills.criteria import LLMJudgedCriteria
from runtime.schema import ContinuationDecision

class DeepDisassembly(Skill):
    @property
    def completion_criteria(self):
        return LLMJudgedCriteria(
            prompt=(
                "Did the agent produce a complete reverse-engineering "
                "result for the requested binary, including (where the "
                "user asked for them): identified algorithm, reconstructed "
                "source, and a verification step?"
            ),
            on_met=ContinuationDecision.SYNTHESIZE,
        )
```

Other skills inherit the default `None` and rely on the generic LLM
judge — fine.

### Change 4 — Wire the evaluator into ContinuationStage

**File:** `src/runtime/stages/continuation.py`

Update `_decide`:

```python
def _decide(self, context: PipelineContext, cfg) -> ContinuationDecision:
    plan = context.plan
    if plan is None or not plan.steps:
        return ContinuationDecision.DONE

    # ── 1. Active criteria from the plan's source skill, if any ──
    criteria = self._active_criteria(plan)
    if criteria is not None:
        outcome = self._evaluate_criteria(criteria, context)
        if outcome == CriteriaOutcome.MET:
            logger.info(f"  continuation: criteria MET ({criteria.__class__.__name__}) → {criteria.on_met.value}")
            return criteria.on_met
        if outcome == CriteriaOutcome.NOT_MET:
            logger.info(f"  continuation: criteria NOT_MET → LOOP")
            return ContinuationDecision.LOOP
        # INCONCLUSIVE → fall through to LLM judge
        logger.info("  continuation: criteria INCONCLUSIVE → LLM judge")

    # ── 2. LLM judge ─────────────────────────────────────────────────
    if cfg.use_llm_judge:
        return self._llm_judge(context, cfg)

    return ContinuationDecision.SYNTHESIZE
```

Add helpers:

```python
def _active_criteria(self, plan: Plan) -> "CompletionCriteria | None":
    """Return the skill's CompletionCriteria when the plan was generated
    by a single skill expansion. Multi-skill plans return None.

    We detect 'single skill' by walking history saved at expand time —
    SkillExpansionStage stamps the active skill on context.skill_used
    (see 'note A' below). If that field is unset (multi-skill or
    planner-only plan), return None.
    """
    skill_name = getattr(self, "_active_skill_name", None)
    # See note A below — context propagation.
    if skill_name is None:
        return None
    skill = self._skill_registry.get(skill_name)
    return skill.completion_criteria if skill else None

def _evaluate_criteria(self, criteria, context) -> CriteriaOutcome:
    from skills.criteria import (
        StructuralCriteria, LLMJudgedCriteria, CriteriaContext, CriteriaOutcome,
    )
    cctx = CriteriaContext(plan=context.plan, user_message=context.user_message)
    if isinstance(criteria, StructuralCriteria):
        return criteria.evaluate(cctx)
    if isinstance(criteria, LLMJudgedCriteria):
        return self._evaluate_llm_criteria(criteria, context)
    return CriteriaOutcome.INCONCLUSIVE

def _evaluate_llm_criteria(self, criteria, context) -> CriteriaOutcome:
    """Run the criteria's prompt through the runtime LLM."""
    from messenger import Messenger
    from runtime.json_extract import extract_json

    system = (
        "You evaluate whether a specific completion criterion is satisfied "
        "by an autonomous agent's executed plan. Return strict JSON: "
        '{"satisfied": true|false, "reason": "..."}.'
    )
    user = (
        f"Original request: {context.user_message}\n\n"
        f"Executed plan ({len(context.plan.steps)} steps):\n{context.plan.summary()}\n\n"
        f"Criterion to evaluate:\n{criteria.prompt}"
    )
    messenger = Messenger()
    messenger.add_user_message(user)
    try:
        response = self._provider.chat(
            messages=messenger.get_messages(), tools=[], system=system,
            label="ContinuationCriteria",
        )
    except Exception as e:
        logger.info(f"  continuation: LLM criteria call failed ({e!r})")
        return CriteriaOutcome.INCONCLUSIVE

    raw = next((b.text for b in response.content if isinstance(b, TextBlock)), "")
    data = extract_json(raw)
    if not isinstance(data, dict) or "satisfied" not in data:
        return CriteriaOutcome.INCONCLUSIVE
    return CriteriaOutcome.MET if bool(data["satisfied"]) else CriteriaOutcome.NOT_MET
```

**Note A — context propagation of "active skill":**

ContinuationStage needs to know which skill (if any) produced the
plan. Add a field to `PipelineContext`:

```python
# src/runtime/pipeline_context.py
# ── Set by SkillExpansionStage ────────────────────────────────────
# Single-skill plans stamp this for ContinuationStage to retrieve
# the right CompletionCriteria. Multi-skill plans leave it None.
active_skill_name: str | None = None
```

In `SkillExpansionStage.run`, when exactly one `skill:` step was
present in the input plan, set `context.active_skill_name = <that name>`.
When zero or two-plus, set to `None`.

Then ContinuationStage reads `context.active_skill_name` in
`_active_criteria`:

```python
def _active_criteria(self, context):
    name = context.active_skill_name
    if name is None:
        return None
    skill = self._skill_registry.get(name)
    return skill.completion_criteria if skill else None
```

(Pass `context` rather than `plan` into `_active_criteria` to make
this work; small rewrite of the helper signature.)

ContinuationStage also needs the `SkillRegistry` injected — update
`__init__` and `agent.py` accordingly:

```python
class ContinuationStage(Stage):
    def __init__(
        self, provider, planner, execution_stage, spinner,
        skill_registry: SkillRegistry,
    ):
        ...
        self._skill_registry = skill_registry
```

### Change 5 — Skill replay in `_build_continuation_plan`

**File:** `src/runtime/stages/continuation.py`

Update the LOOP-time plan builder to try skill replay first:

```python
def _build_continuation_plan(self, context: PipelineContext) -> Plan | None:
    plan = context.plan
    if plan is None or not plan.steps:
        return None

    # Tier 1: skill replay
    name = context.active_skill_name
    if name is not None:
        skill = self._skill_registry.get(name)
        if skill is not None:
            from skills.base import SkillContext
            sctx = SkillContext(
                original_query=plan.original_query,
                skill_args={},
                starting_step_number=1,
            )
            replay_steps = skill.continuation_steps(sctx, plan.steps)
            if replay_steps:
                return Plan(
                    original_query=plan.original_query,
                    steps=replay_steps,
                    risk=getattr(plan, "risk", "low"),
                )

    # Tier 2: planner replan
    last_step = plan.steps[-1]
    new_steps = self._planner.replan(
        plan, last_step, "continuation requested by ContinuationStage"
    )
    if not new_steps:
        return None
    return Plan(
        original_query=plan.original_query,
        steps=new_steps,
        risk=getattr(plan, "risk", "low"),
    )
```

### Change 6 — Enable LLM judge by default

Now that we have skill criteria, flip the config default so the
generic judge runs as a sanity net for plans without criteria:

```yaml
# config.yml
runtime:
  continuation:
    use_llm_judge: true     # was false in 0079e
```

The new default in `ContinuationConfig`:

```python
use_llm_judge: bool = True
```

## Verification

```bash
pytest -x -q

# 1. test-reconstruction skill: run a query that diverges initially.
#    ContinuationStage should log:
#      "criteria NOT_MET → LOOP"
#    On the next iteration after a fix, expect:
#      "criteria MET (StructuralCriteria) → synthesize"
#
#    (This is the fix-loop that phase 0079i exercises end-to-end.)

# 2. read-modify-write skill: write a file. ContinuationStage should log:
#      "criteria MET (StructuralCriteria) → done"
#    No synthesis runs — same behavior the old requires_synthesis=False
#    produced.

# 3. deep-disassembly skill: run a recon query. ContinuationStage should
#    invoke the LLM-judged criterion and either MET → synthesize or
#    INCONCLUSIVE → fall through to the generic LLM judge.

# 4. Multi-skill plan (planner emits two skill calls): active_skill_name
#    is None; ContinuationStage uses the generic LLM judge only.
```

## Done when

- [ ] `src/skills/criteria.py` exists with `StructuralCriteria` and
      `LLMJudgedCriteria`.
- [ ] `Skill.completion_criteria` and `Skill.continuation_steps`
      defined with default returns.
- [ ] At least three skills declare criteria (test-reconstruction,
      read-modify-write, deep-disassembly).
- [ ] `PipelineContext.active_skill_name` exists and is set by
      `SkillExpansionStage` for single-skill plans.
- [ ] ContinuationStage evaluates criteria before LLM judge.
- [ ] `_build_continuation_plan` tries skill replay before planner
      replan.
- [ ] `config.runtime.continuation.use_llm_judge = true` by default.
- [ ] `pytest` green.
- [ ] read-modify-write produces the same end-state it did before
      0079f deleted `requires_synthesis=False` (no synthesis runs).

## Out of scope

- Aggregating criteria across multi-skill plans (deferred — see
  0079d §15).
- Streaming evaluation. Criteria are blocking; that's fine.
- A criteria DSL or YAML schema. Keep them as Python dataclasses for
  now; convert to data later if reuse demands it.
