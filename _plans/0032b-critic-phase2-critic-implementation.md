# 0032b — Plan Critic Phase 2: Critic Implementation

**Date**: 2026-04-15
**Status**: Implemented
**Parent**: 0032

## Changes

### 1. `runtime/schema.py` — Critic types
- `CriticVerdict` enum: `APPROVED`, `CHALLENGED`
- `CriticChallenge` dataclass: `step`, `tool`, `challenge`, `suggestion`
- `CriticResult` dataclass: `verdict`, `reasoning`, `challenges`

### 2. `runtime/prompts.py` — Critic prompts
- `CRITIC_SYSTEM_PROMPT`: stern adversarial prompt with 6 evaluation criteria:
  1. JUSTIFY IT — what unique value does this step add?
  2. PROPORTIONALITY — is the tool proportionate to the task?
  3. REDUNDANCY — does this duplicate a lighter step?
  4. ENVIRONMENT — will this tool actually work on this system?
  5. KNOWLEDGE CHECK — does the model already know this from training?
  6. ORDERING — correct dependency order?
- Demands specific challenges, not vague "might not be needed"
- Output format: `{"verdict": "approved"|"challenged", ...}`
- `CRITIC_USER_TEMPLATE`: includes original query, formatted plan, and tool descriptions

### 3. `runtime/critic.py` — PlanCritic class
- `review(plan) -> CriticResult`: main entry point
  - Formats plan and tool descriptions
  - Sends to runtime provider (cheap model)
  - Parses response into CriticResult
  - Logs verdict and individual challenges
- `format_challenges(result) -> str`: formats challenges for planner revision
- `_format_plan()`: renders plan as readable text for the critic
- `_format_tool_descriptions()`: lists all tools with descriptions
- `_parse()`: JSON parsing with fence handling, defaults to APPROVED on failure

### 4. `config.py` / `config.yml` — Critic config
- New `PlanCriticConfig` dataclass with `enabled: bool`
- Added to `RuntimeConfig`
- Added `plan_critic.enabled: true` to config.yml
