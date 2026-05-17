# 0037e — Council: Phase 5 — PlanCritic Integration

**Date**: 2026-04-18
**Status**: Implemented
**Parent**: 0037

## What was built

### `src/runtime/critic.py` — complete rewrite

**`PlanCriticAdapter(DeliberationAdapter[CriticResult])`**

Implements all five required adapter methods:

| Method | What it does |
|--------|-------------|
| `system_prompt()` | Returns `CRITIC_SYSTEM_PROMPT` (unchanged) |
| `build_prompt(plan, prior_rounds)` | Formats `CRITIC_USER_TEMPLATE` — same prompt as before |
| `parse_response(raw)` | JSON parse → `CriticResult` (same logic as old `_parse()`) |
| `synthesize(decisions, threshold)` | Consensus downgrade algorithm (see below) |
| `decisions_converged(decisions)` | All same verdict AND same challenged step set |
| `summarize_decision(decision)` | `{"verdict": ..., "challenges": [...]}` for logs/metrics |

**Consensus synthesis algorithm:**

For each step challenged by at least one councillor:
- `ratio = k / N` (fraction that challenged)
- `ratio >= threshold` (default 0.67) → keep suggestion at full strength
- `ratio > 1/N` but below threshold → downgrade one level: drop→replace, replace→justify, justify→discard
- Lone wolf (k==1, N>2) → floor at justify

This means `strings` for binary analysis is safe: if 1 of 3 councillors challenges it
while the other 2 approve, the challenge is floored at "justify" rather than "drop".
The planner is asked to defend it rather than having it silently stripped.

**`PlanCritic`**

Simplified to a single method `review(plan)`:
- Creates `PlanCriticAdapter(registry, plan)`
- Creates `Council(adapter, config=config.runtime.council)`
- Calls `council.deliberate(plan, context="plan_critic", query=...)`
- Attaches `council_run_id` to the returned `CriticResult`
- Logs APPROVED / CHALLENGED with full challenge text (no truncation)

Removed:
- `_single_review()` — replaced by `Council._run_round()`
- `_merge_results()` — replaced by `PlanCriticAdapter.synthesize()`
- Constructor no longer takes provider arguments — providers come from `config.runtime.council`

`format_challenges()` kept unchanged — used by agent to format for planner revision.

### `src/runtime/schema.py`

`CriticResult` gains `council_run_id: str | None = None` — set by `PlanCritic.review()`.
Allows callers to correlate the critic result with the `_metrics/` JSONL record.

### `src/agent.py`

`PlanCritic(self.registry)` — no longer passes providers.

## What the logs now show

For a 3-councillor independent council reviewing a plan:

```
── Plan critic ────────────────────────────────────────────
  [council] independent — councillors: ['claude', 'gpt', 'grok']
  [council] ── Round 1 ────────────────────────────────────────
  [council][claude] querying anthropic...
  [council][claude] response:
  {"verdict": "challenged", "challenges": [{"step": 2, "tool": "strings", ...}]}
  [council][claude] decision: {"verdict": "challenged", "challenges": [...]}
  [council][gpt] querying openai...
  [council][gpt] response:
  {"verdict": "approved", "reasoning": "strings is standard for binary analysis"}
  [council][gpt] decision: {"verdict": "approved", ...}
  [council][grok] querying grok...
  [council][grok] response:
  {"verdict": "challenged", "challenges": [{"step": 2, "tool": "strings", ...}]}
  [council][grok] decision: {"verdict": "challenged", "challenges": [...]}
  [synth] synthesis:
    [synth] step 2: 2/3 challenged (67% ≥ threshold 67%) → keep drop
  [synth] final: {"verdict": "challenged", "challenges": [...]}
  critic: CHALLENGED (1 challenge(s))
    step 2 [strings]: drop — <full challenge text>
```

With a threshold of 0.67, 2/3 councillors agreeing is exactly at the threshold — the
challenge is kept. If only 1 of 3 challenged (33% < 67%), it would be downgraded to justify.

## Telemetry

Every council run writes to `_metrics/<session_id>.jsonl` with:
- Per-councillor decisions (what each model said)
- agreement_map (challengers, approvers, ratio per step)
- synthesis_trace (human-readable downgrade decisions)
- final_verdict
- council_run_id (links CriticResult to the metrics record)
