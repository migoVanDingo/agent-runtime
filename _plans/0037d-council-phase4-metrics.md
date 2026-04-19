# 0037d — Council: Phase 4 — Metrics + User Outcome Tracking

**Date**: 2026-04-18
**Status**: Implemented
**Parent**: 0037

## What was built

### Metrics auto-write on every council run

In `Council._build_result()`:
```python
from runtime.council_metrics import get_metrics_writer
writer = get_metrics_writer()
if writer:
    writer.record_run(metrics)
```

Every `council.deliberate()` call automatically persists to `_metrics/<session_id>.jsonl`.

### `CouncilMetricsWriter` (`src/runtime/council_metrics.py`)

- `record_run(metrics)` — appends one JSON line with full run details
- `record_user_outcome(run_id, user_action, sided_with, overrode)` — reads JSONL, finds the
  matching run_id, updates `user_outcome` field, rewrites
- `init_metrics_writer(session_id)` / `get_metrics_writer()` — singleton, initialized in
  `configure_logging()` so it's ready before the first council run

### JSONL record schema

```json
{
  "ts": "2026-04-18T...",
  "session_id": "SES...",
  "run_id": "a3f2b1c8",
  "context": "plan_critic",
  "query": "...",
  "mode": "independent",
  "rounds_completed": 1,
  "councillors": ["claude", "gpt", "grok"],
  "decisions": {
    "claude": {"verdict": "challenged", "challenges": [...]},
    "gpt":    {"verdict": "approved"},
    "grok":   {"verdict": "challenged", "challenges": [...]}
  },
  "agreement_map": {
    "step_2": {"challengers": ["claude", "grok"], "approvers": ["gpt"], "ratio": 0.67}
  },
  "synthesis_trace": ["step 2: 2/3 challenged (67% ≥ threshold) → keep drop"],
  "final_verdict": "challenged",
  "user_outcome": null
}
```

### `Escalation` schema update (`src/runtime/escalation.py`)

Added two optional fields:
- `council_run_id: str | None` — links the escalation to a metrics record
- `council_councillor_labels: list[str] | None` — which councillors drove the challenge

### `CLIUserGate.prompt()` user outcome hook

When `escalation.council_run_id` is set, after the user answers, calls:
```python
writer.record_user_outcome(
    run_id=escalation.council_run_id,
    user_action="approved" | "denied",
    sided_with=[],          # populated by the caller who knows the challenge context
    overrode=all_labels,
)
```

The `sided_with` list is left empty at the gate level — the Phase 5 integration
(PlanCriticAdapter → PlanCritic) computes sided_with correctly because it knows which
councillors challenged vs approved the specific step.

### `_metrics/` gitignored

Added to `.gitignore` in Phase 1.
