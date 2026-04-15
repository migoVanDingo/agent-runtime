# 0026 — Runtime Infrastructure Phase 4: Plan Validator

## What

Code-only structural validation of plans before execution begins. No LLM
call — catches malformed plans that the planner occasionally produces.

## Checks

1. **Step count** — must not exceed `planning.max_steps`
2. **Sequential numbering** — steps must be numbered 1..N
3. **Action type validity** — each non-conversation action_type must be
   a registered toolset name
4. **Non-empty descriptions** — every step needs a description
5. **Duplicate detection** — consecutive steps with identical descriptions
   are flagged

## Changes

### New files

- **`src/runtime/validator.py`** — `PlanValidator` class:
  - `__init__(registered_toolsets: set[str])` — takes the set of
    registered toolset names for action_type checking
  - `validate(plan) -> ValidationResult` — runs all checks, returns
    VALID or INVALID with specific feedback
  - Respects `runtime.plan_validator.enabled` config toggle

### Modified files

- **`src/agent.py`**:
  - Imports: added `PlanValidator`, `ValidationStatus`
  - `__init__`: creates `self.validator` with the registry's toolset names
  - `call()`: after planner produces a plan, runs validation. On INVALID:
    retries the planner once with the validation feedback appended to the
    user message. If the retry also fails validation (or planner returns
    None), falls back to direct execution.

## Validation flow in agent.call()

```
planner.plan() → plan
  → validator.validate(plan)
    ├── VALID → _execute_plan(plan)
    └── INVALID →
          planner.plan(message + feedback) → retry_plan
            → validator.validate(retry_plan)
              ├── VALID → _execute_plan(retry_plan)
              └── INVALID → fall back to direct execution
```

## What does not change

- Planner — unchanged (validator catches its mistakes externally)
- Plan schema — unchanged
- Executor — unchanged
