# 0034d — Phase 4: Multi-Model Consensus (Council Mode Lite)

**Date**: 2026-04-17
**Status**: Implemented
**Parent**: 0034

## Motivation

The Council paper demonstrates that heterogeneous multi-agent consensus reduces hallucinations by 35.9%. Our system used single-model decision-making everywhere. Phase 4 adds a pragmatic subset: dual-critic review for high-risk plans, and confidence scoring on monitor decisions.

## Changes

### 4a. Dual-Model Plan Validation

**Modified**: `src/runtime/critic.py`

- `PlanCritic.__init__` now accepts optional `consensus_provider` parameter
- Refactored `review()` into:
  - `_build_user_turn(plan)` — builds the prompt once
  - `_single_review(user_turn, provider, label)` — runs one critic
  - `_merge_results(r1, r2)` — merges two CriticResults
- `review()` logic:
  - Always runs primary critic (runtime provider)
  - If `plan.risk == "high"` AND `consensus_provider` is set AND `consensus_on_high_risk` config is true → runs second critic with consensus provider
  - Merges challenges: if either critic challenges a step, it stays challenged. When both challenge the same step, the stronger suggestion wins (drop > replace > justify)

**Modified**: `src/agent.py`
- Passes `consensus_provider=self.provider` (main provider) to PlanCritic
- Runtime provider (cheap model) = critic-1, Main provider (strong model) = critic-2 on high-risk plans

**Modified**: `src/config.py`, `config.yml`
- Added `consensus_on_high_risk: bool` to PlanCriticConfig (default: true)

**Cost**: +1 LLM call on high-risk plans only. Low-risk and moderate-risk plans are unaffected.

### 4b. Step-Level Confidence Scoring

**Modified**: `src/runtime/schema.py`
- Added `confidence: float = 1.0` to StepAssessment

**Modified**: `src/runtime/prompts.py`
- Monitor prompt now asks for `"confidence": 0.0-1.0`

**Modified**: `src/runtime/monitor.py`
- `_parse()` extracts and clamps confidence (0.0-1.0)
- `_llm_assess()` logs confidence alongside decision
- **Low-confidence RETRY override**: if confidence < 0.5 and decision is RETRY → overrides to SKIP. Rationale: if the monitor isn't sure the step failed, don't waste a retry attempt.

## Critic Decision Flow (After Phase 4)

```
Plan arrives at critic
  ├─ risk == "low" (and skip_low_risk=true) → skip critic entirely
  ├─ risk == "moderate" → single critic (runtime provider)
  └─ risk == "high" →
       ├─ critic-1 (runtime provider, cheap)
       ├─ critic-2 (main provider, strong)
       └─ merge: union of challenges, stronger suggestion wins
```

## Monitor Decision Flow (After Phase 4)

```
Step result → heuristics
  ├─ clean → auto-CONTINUE
  └─ flagged → LLM assessment (with confidence)
       ├─ CONTINUE (any confidence) → proceed
       ├─ RETRY (confidence ≥ 0.5) → retry step
       ├─ RETRY (confidence < 0.5) → override to SKIP
       ├─ REPLAN → replan remaining steps
       ├─ SKIP → skip step
       └─ ESCALATE → prompt user
```
