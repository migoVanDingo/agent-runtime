# 0079f — Phase F: Plan/Step Schema Cleanup

> **Read first:** `_plans/0079-runtime-as-god.md` §0.
> **Depends on:** 0079e (ContinuationStage must be in place — it
> replaces the field this phase deletes).
> Phases 0079g and 0079h depend on this.

## Goal

Make `Plan` and `Step` **descriptive only**. Delete every field that
prescribes infrastructure behavior:

- `Plan.requires_synthesis` (now ContinuationStage's job)
- `StepFlags.retry`, `StepFlags.escalate`, `StepFlags.defer`
  (currently never read by execution.py to control flow — vestigial)

Keep the runtime-state fields on `Step.flags` (`retry_count`, `deferred`,
`skipped`) — those are bookkeeping the runtime owns and writes.

This addresses **DRIFT-2** and **DRIFT-3**.

## Verification of "vestigial" claim

Before deleting, confirm by grep:

```bash
rg -n "\.flags\.retry\b" src/        # should show only assignments / dataclass field
rg -n "\.flags\.escalate\b" src/     # ditto
rg -n "\.flags\.defer\b" src/        # ditto
```

What you should find:
- `src/planning/schema.py` — dataclass declaration, `to_dict`, `from_dict`
- `src/planning/schema.py` — `PLAN_JSON_SCHEMA` for OpenAI structured output
- Possibly `src/runtime/persistence.py` — recording the flags

What you should **not** find:
- Any `if step.flags.retry:` / `if step.flags.escalate:` / `if step.flags.defer:`
  conditional in `src/runtime/stages/execution.py` or anywhere in
  `src/runtime/`.

If a behavioral reader DOES exist, **stop and surface it**: the
"vestigial" claim is wrong and this phase needs to migrate that
behavior elsewhere first. Per the brief verification: as of commit
`0a9a064`, only `flags.retry_count`, `flags.deferred`, `flags.skipped`
are read by execution. The three booleans are write-only.

## Files to change

| File | Why |
|------|-----|
| `src/planning/schema.py` | Remove `requires_synthesis` and prescriptive `StepFlags` booleans. Update `PLAN_JSON_SCHEMA`. |
| All skill files in `src/skills/implementations/` | Remove `requires_synthesis=...` and `flags=StepFlags(...)` constructor args (already done in 0079c except the field — finish here). |
| `src/runtime/stages/synthesizer.py` | Remove the `requires_synthesis` gate (line 45). Stage now runs whenever the pipeline reaches it. |
| `src/runtime/stages/continuation.py` | Remove the legacy shim added in 0079e. |
| `src/runtime/stages/execution.py` | Remove any remaining references to `plan.requires_synthesis` (lines ~159, ~409). Remove logging of `requires_synthesis` in event payloads. |
| `src/runtime/stages/council.py` | Remove the `if plan.requires_synthesis:` coherence check at lines 163-178. The check was protecting against a structurally incoherent plan (synthesis-only with no data steps); replace with a simpler check (see §3). |
| `src/runtime/persistence.py` | If it stores `requires_synthesis`, remove. |
| `src/planning/planner.py` | Update prompts/parsing to not mention `requires_synthesis`. |
| `src/planning/prompts.py` | Same. |

## Detailed changes

### Change 1 — `Plan` dataclass and JSON schema

**File:** `src/planning/schema.py`

Current `Plan` (lines 166-186):

```python
@dataclass
class Plan:
    original_query: str
    steps: list[Step]
    requires_synthesis: bool = True
    risk: str = "low"

    def to_dict(self) -> dict:
        return {
            "original_query": self.original_query,
            "steps": [s.to_dict() for s in self.steps],
            "requires_synthesis": self.requires_synthesis,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Plan:
        return cls(
            original_query=data["original_query"],
            steps=[Step.from_dict(s) for s in data["steps"]],
            requires_synthesis=data.get("requires_synthesis", True),
        )

    def summary(self) -> str:
        ...
```

After:

```python
@dataclass
class Plan:
    original_query: str
    steps: list[Step]
    # risk is set by the routing classifier; council reads it.
    # It is descriptive (the assessment of the request), not prescriptive.
    risk: str = "low"

    def to_dict(self) -> dict:
        return {
            "original_query": self.original_query,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Plan:
        return cls(
            original_query=data["original_query"],
            steps=[Step.from_dict(s) for s in data["steps"]],
        )

    def summary(self) -> str:
        # unchanged
        ...
```

`PLAN_JSON_SCHEMA` (lines 7-60): remove `requires_synthesis` from
`properties` and from `required`.

### Change 2 — `StepFlags` dataclass

**File:** `src/planning/schema.py:87-115`

Current:

```python
@dataclass
class StepFlags:
    retry: bool = False
    escalate: bool = False
    defer: bool = False
    retry_count: int = 0
    deferred: bool = False
    skipped: bool = False

    def to_dict(self) -> dict:
        return {
            "retry": self.retry,
            "escalate": self.escalate,
            "defer": self.defer,
            "retry_count": self.retry_count,
            "deferred": self.deferred,
            "skipped": self.skipped,
        }

    @classmethod
    def from_dict(cls, data: dict) -> StepFlags:
        return cls(
            retry=data.get("retry", False),
            escalate=data.get("escalate", False),
            defer=data.get("defer", False),
            retry_count=data.get("retry_count", 0),
            deferred=data.get("deferred", False),
            skipped=data.get("skipped", False),
        )
```

After (rename to `StepRuntimeState` to make its purpose obvious and
prevent regression):

```python
@dataclass
class StepRuntimeState:
    """Runtime-managed state for a step. Never set by the planner or skills.

    The execution stage and monitor mutate these as the step runs.
    """
    retry_count: int = 0
    deferred: bool = False
    skipped: bool = False

    def to_dict(self) -> dict:
        return {
            "retry_count": self.retry_count,
            "deferred": self.deferred,
            "skipped": self.skipped,
        }

    @classmethod
    def from_dict(cls, data: dict) -> StepRuntimeState:
        return cls(
            retry_count=data.get("retry_count", 0),
            deferred=data.get("deferred", False),
            skipped=data.get("skipped", False),
        )


# Alias kept for one phase to ease the rename. Delete in a follow-up.
StepFlags = StepRuntimeState
```

The alias keeps `step.flags` working. Update the dataclass annotation
on `Step` to use the new name:

```python
@dataclass
class Step:
    step: int
    description: str
    action_type: ActionType
    tool: str | None = None
    produces: str | None = None
    status: StepStatus = StepStatus.PENDING
    result: str | None = None
    error: str | None = None
    flags: StepRuntimeState = field(default_factory=StepRuntimeState)
```

In `PLAN_JSON_SCHEMA`, the `flags` block (lines 41-50) currently
requires `retry`, `escalate`, `defer`. Drop the entire `flags` property
from the schema — the planner never set runtime state and shouldn't.
The dataclass default factory takes care of construction.

```python
# PLAN_JSON_SCHEMA "items" properties:
"properties": {
    "step": {"type": "integer"},
    "description": {"type": "string"},
    "action_type": { ... },
    "tool": {"type": ["string", "null"]},
    "produces": {"type": ["string", "null"]},
},
"required": ["step", "description", "action_type", "tool", "produces"],
```

### Change 3 — Synthesizer gate removal

**File:** `src/runtime/stages/synthesizer.py:44-46`

Current:
```python
def run(self, context: PipelineContext) -> StageResult:
    if context.plan is None or not context.plan.requires_synthesis:
        return StageResult(status=StageStatus.OK, updated_context=context)
```

After:
```python
def run(self, context: PipelineContext) -> StageResult:
    # SynthesizerStage runs whenever the pipeline reaches it.
    # ContinuationStage decides whether we get here (returns OK)
    # or skip it (returns DONE).
    if context.plan is None:
        return StageResult(status=StageStatus.OK, updated_context=context)
```

Also drop the `_plan_had_failures` path that reads `s.flags.retry_count`
and `s.flags.skipped` — those still exist (runtime state) so leave
that helper alone. Just confirm it doesn't break.

### Change 4 — ContinuationStage shim removal

**File:** `src/runtime/stages/continuation.py`

In `_decide` and `_fall_through_legacy`, delete:

```python
legacy = getattr(plan, "requires_synthesis", True)
if not legacy:
    return ContinuationDecision.DONE
```

and

```python
if not getattr(plan, "requires_synthesis", True):
    return StageResult(status=StageStatus.DONE, updated_context=context)
```

Replace with the actual decision logic (which at this phase is just
`SYNTHESIZE` unless the plan is empty):

```python
def _decide(self, context: PipelineContext, cfg) -> ContinuationDecision:
    plan = context.plan
    if plan is None or not plan.steps:
        return ContinuationDecision.DONE
    if cfg.use_llm_judge:
        return self._llm_judge(context, cfg)
    return ContinuationDecision.SYNTHESIZE
```

### Change 5 — ExecutionStage references

**File:** `src/runtime/stages/execution.py`

- Line 142 (`if not context.plan.requires_synthesis:`) — already removed
  in phase 0079e per its instructions. Confirm.
- Line 159: event payload contains `"requires_synthesis": plan.requires_synthesis`.
  Remove the key from the payload dict.
- Lines 409-413: branch on `plan.requires_synthesis`. Remove. Replace
  the whole final-return tail with:

  ```python
  logger.info(banner("Execution complete"))
  last_completed = next(
      (s for s in reversed(queue) if s.status == StepStatus.COMPLETED and s.result),
      None,
  )
  return last_completed.result if last_completed else ""
  ```

  (i.e., always return a string; ContinuationStage may overwrite
  context.response on LOOP.)

### Change 6 — CouncilStage coherence check

**File:** `src/runtime/stages/council.py:163-178`

Current check:
```python
if plan.requires_synthesis:
    from planning.schema import ActionType
    data_steps = [
        s for s in plan.steps
        if s.action_type != ActionType.CONVERSATION
    ]
    if not data_steps:
        logger.info(...)
        return StageResult(status=StageStatus.ABORT, ...)
```

After (drop the `requires_synthesis` gate; the integrity check is
useful regardless):

```python
from planning.schema import ActionType
data_steps = [
    s for s in plan.steps
    if s.action_type != ActionType.CONVERSATION
]
if not data_steps:
    logger.info(
        "  council: plan stripped to CONVERSATION-only steps with no "
        "data-gathering — aborting to fallback"
    )
    return StageResult(
        status=StageStatus.ABORT,
        updated_context=context,
        reason="Plan stripped to conversation-only: no data-gathering steps remain",
    )
```

### Change 7 — Persistence and event payloads

**File:** `src/runtime/persistence.py`

Search the file for `requires_synthesis`. Remove the field from any
record/insert. If the underlying SQL schema stores it, leave the column
in place but stop writing it (passing `None` is fine if the column is
nullable; otherwise drop the column with a small Alembic migration —
see `_plans/0074-phase-j-persistence.md` for migration patterns).
Recommendation: keep the column nullable; don't migrate yet.

**File:** any caller of `RuntimeEvent` that includes `requires_synthesis`
(grep `requires_synthesis` repo-wide) — remove the key.

### Change 8 — Skill code cleanup

**Files:** `src/skills/implementations/*.py` (after phase 0079c
landed these files)

Remove:
- `requires_synthesis=True` / `requires_synthesis=False` from any `Plan(...)` constructions. (Skills don't construct Plans anymore — they emit step lists. Confirm via `rg "Plan\(" src/skills/`.)
- `flags=StepFlags(...)` arguments to `Step(...)`. The default factory provides empty runtime state.

After this phase, no skill file references `requires_synthesis` or `StepFlags` (the alias).

### Change 9 — Planner prompts

**File:** `src/planning/prompts.py`

Search for any mention of `requires_synthesis` in the planner system
prompt or user template. Remove. The planner no longer emits this
field; the JSON schema enforces.

### Change 10 — Repo sweep

```bash
rg -n "requires_synthesis" src/
# Should return ZERO hits after this phase, except maybe a comment
# in continuation.py saying "deleted in 0079f"; remove that too.

rg -n "StepFlags" src/
# Should return only:
#   - the alias declaration in planning/schema.py
#   - any test fixtures (acceptable; they use the alias)

rg -n "flags\.retry\b" src/
rg -n "flags\.escalate\b" src/
rg -n "flags\.defer\b" src/
# Each should return ZERO hits (the booleans are gone).
```

## Verification

```bash
pytest -x -q
python -m src.main <<< "what is 2+2"
python -m src.main <<< "list files in /tmp"
# Run a planner-driven multi-step query that previously had requires_synthesis=True;
# behavior should be identical (ContinuationStage decides SYNTHESIZE).

# Validate the planner's JSON output:
# Inspect the LLM response for a complex query and confirm no
# 'requires_synthesis' or 'flags' keys appear.
```

## Done when

- [ ] `Plan` has no `requires_synthesis` field.
- [ ] `StepFlags`/`StepRuntimeState` has only runtime-state fields
      (`retry_count`, `deferred`, `skipped`).
- [ ] `PLAN_JSON_SCHEMA` removes `requires_synthesis` and the entire
      `flags` block.
- [ ] No stage reads `Plan.requires_synthesis`.
- [ ] No stage reads `Step.flags.retry`/`escalate`/`defer`.
- [ ] `pytest` green.
- [ ] End-to-end behavior is identical to phase 0079e for any flow
      that worked there. (The semantic change — synthesis decision
      now belongs to ContinuationStage — already happened in 0079e;
      this phase only deletes the now-dead transitional code.)

## Out of scope

- Removing the `StepFlags` alias. Keep it for one phase to make the
  rename safe; delete in a follow-up sweep.
- Renaming `Step.flags` to `Step.runtime_state`. Same reasoning;
  follow-up.
