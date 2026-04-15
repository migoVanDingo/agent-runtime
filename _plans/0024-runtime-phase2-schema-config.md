# 0024 — Runtime Infrastructure Phase 2: Schema + Config

## What

Create the `src/runtime/` module with schema definitions for all runtime
components, and add the runtime configuration section to `config.yml` and
`config.py`. This is the foundation that phases 3-6 build on.

## Changes

### New files

- **`src/runtime/__init__.py`** — empty, establishes the package.

- **`src/runtime/schema.py`** — all runtime dataclasses and enums:
  - `StepDecision` enum: CONTINUE, RETRY, REPLAN, DEFER, SKIP, ESCALATE
  - `StepAssessment`: decision + reason + optional suggestion
  - `ValidationStatus` enum: VALID, INVALID
  - `ValidationResult`: status + optional feedback string
  - `FidelityLevel` enum: FULL, COMPRESSED, PLACEHOLDER
  - `Importance` enum: CRITICAL, HIGH, MEDIUM, LOW
  - `ScoredMessage`: index, message, score, importance, fidelity, token_estimate

### Modified files

- **`src/planning/schema.py`** — `StepFlags` gains three new fields:
  - `retry_count: int = 0` — tracks retries for max enforcement
  - `deferred: bool = False` — whether this step has been deferred
  - `skipped: bool = False` — whether this step was skipped
  - `to_dict()` and `from_dict()` updated to include new fields.

- **`config.yml`** — new `runtime:` section with four sub-sections:
  - `intent_classifier`: enabled, context_window (6)
  - `plan_validator`: enabled
  - `execution_monitor`: enabled, max_step_retries (2), max_defers_per_step (1)
  - `context_manager`: enabled, message_budget_tokens (16384),
    half_life_turns (10), threshold_high (0.45), threshold_mid (0.25),
    compressed_max_chars (300)

- **`src/config.py`** — new dataclasses:
  - `IntentClassifierConfig`
  - `PlanValidatorConfig`
  - `ExecutionMonitorConfig`
  - `ContextManagerConfig`
  - `RuntimeConfig` (groups all four)
  - `AppConfig` gains `runtime: RuntimeConfig` field
  - `load_config()` parses the new YAML section

## What does not change

- Agent behavior — nothing reads these configs yet
- Existing planning schema — new StepFlags fields all default to
  their previous-equivalent values (0/False)
