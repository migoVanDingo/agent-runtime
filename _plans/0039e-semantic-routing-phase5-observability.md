# Phase 5: Observability

## What Was Built

Every plan-mode request now logs a single `workflow routing: <path>` line that identifies exactly which routing path was used. Makes it trivial to audit routing decisions in session logs.

## Change

### `agent.py`
Replaced the two separate "using workflow template" / "no workflow match" log lines with a unified:

```
workflow routing: classifier_hint
workflow routing: classifier_hint_direct
workflow routing: regex
workflow routing: fallback
workflow routing: planner
```

## Reading the Logs

Search for `workflow routing:` in any session log to see the routing decision for each turn:

- `classifier_hint` — semantic match, regex confirmed
- `classifier_hint_direct` — semantic match, regex missed but workflow generated plan directly
- `regex` — pattern match (existing behavior)
- `fallback` — both classifier and regex missed; dedicated selector call matched
- `planner` — no workflow match; LLM planner invoked

Together with the classifier's `mode:`, `risk:`, and `workflow hint:` lines, this gives a complete picture of the routing decision chain for every request.
