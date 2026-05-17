# 0037b — Council: Phase 2 — Debate Mode

**Date**: 2026-04-18
**Status**: Implemented
**Parent**: 0037

## What was built

Debate mode is implemented in `src/runtime/council.py` alongside Phase 1.

### Flow

```
Round 1: all councillors generate blind (identical to independent mode)
         → check convergence
         → early exit if converged and early_exit_on_consensus=True

Round 2..max_rounds:
    for each councillor:
        prompt = adapter.build_prompt(input, prior_rounds=None)
                 + adapter.format_prior_rounds(prior_rounds=[round1, ...])
    → each councillor sees all prior round outputs from all councillors
    → check convergence

After max_rounds or convergence:
    synthesize final_round.decisions
```

### `_deliberate_debate()`

- Loops `range(1, max_rounds+1)`
- Round 1: `prior_rounds=None` (same as independent)
- Rounds 2+: `prior_rounds=rounds_so_far`
- Checks `rnd.converged and early_exit` after each round
- Passes all accumulated rounds to `_build_result()`

### `format_prior_rounds()` (on `DeliberationAdapter`)

Default implementation appends to the prompt:
```
--- Prior Round Responses ---
[claude, round 1]: <full raw response>
[gpt, round 1]:    <full raw response>
[grok, round 1]:   <full raw response>

You have seen the above responses from other councillors. Reconsider your
position. If their arguments are sound, you may revise. If you still
disagree, explain specifically why. Return the same JSON format as before.
```

Adapters can override `format_prior_rounds()` to customize this.

### Convergence check

`decisions_converged(decisions)` is adapter-defined. `PlanCriticAdapter` (Phase 5)
implements it as: all verdicts identical AND same set of challenged steps.

### Anchoring bias note (from design doc)

There's a risk councillors anchor to Round 1 responses. Mitigation options (not yet
implemented): randomize the order other responses are shown, or show summaries rather
than full raw text. Flag for future tuning if debate mode shows anchoring in practice.

## Config

```yaml
council:
  mode: debate
  debate:
    max_rounds: 3
    early_exit_on_consensus: true
```
