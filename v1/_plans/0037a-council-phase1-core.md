# 0037a — Council: Phase 1 — Core Primitive + Independent Mode

**Date**: 2026-04-18
**Status**: Implemented
**Parent**: 0037

## What was built

### `src/runtime/council.py` (new)

Core data types:
- `Councillor` — provider, label, optional model override
- `CouncillorDecision` — label, provider, model, raw_response, parsed (T), round_number
- `CouncilRound` — round_number, decisions list, converged flag
- `CouncilRunMetrics` — full run record for metrics persistence (run_id, context, query, mode, rounds, per-councillor decisions, agreement_map, synthesis_trace, final_verdict, user_outcome)
- `CouncilResult[T]` — rounds, final decision, agreement_map, synthesis_trace, metrics

`DeliberationAdapter[T]` abstract base:
- `system_prompt()` — system prompt sent to every councillor
- `build_prompt(input, prior_rounds?)` — builds the user-turn prompt
- `parse_response(raw)` — parses councillor response to T
- `synthesize(decisions, threshold)` — produces (final T, agreement_map, trace)
- `decisions_converged(decisions)` — used for debate early-exit
- `summarize_decision(decision)` — JSON-serializable summary for logs/metrics
- `format_prior_rounds(prior_rounds)` — default debate prompt suffix (can be overridden)

`Council[T]`:
- `deliberate(input, context, query)` — dispatches to independent or debate mode
- `_run_round()` — calls each councillor sequentially, logs full raw response + decision summary
- `_build_result()` — runs adapter.synthesize(), assembles CouncilResult, logs synthesis trace
- Councillors are instantiated from `config.councillors` (CouncillorConfig list)
- Uses `get_provider(councillor.provider, councillor.model)` — already supports all providers

### `src/runtime/council_metrics.py` (new)

- `CouncilMetricsWriter` — writes to `_metrics/<session_id>.jsonl`
- `record_run(metrics)` — appends one JSON line per council run
- `record_user_outcome(run_id, action, sided_with, overrode)` — updates existing record in place
- `init_metrics_writer(session_id)` / `get_metrics_writer()` — singleton accessors
- `_metrics/` directory auto-created on first write

### `src/config.py`

- Added `CouncillorConfig`, `DebateConfig`, `CouncilConfig` dataclasses
- `CouncilConfig.councillors` is a flat list — supports N-same, M-hetero, N+M mixed freely
- `RuntimeConfig` gains `council: CouncilConfig` field
- `load_config()` parses `runtime.council` block from YAML

### `config.yml`

Added `runtime.council` block:
- Default councillors: anthropic/claude, openai/gpt, grok/grok
- Default mode: independent
- Default consensus_threshold: 0.67
- Comments explain same-provider vs heterogeneous tradeoffs

### `.gitignore`

Added `_metrics` (session-local, not committed).

## What's NOT in this phase

- Debate mode loop (Phase 2)
- Color logger (Phase 3)
- Metrics wiring + user outcome hooks (Phase 4)
- PlanCriticAdapter + PlanCritic integration (Phase 5)
