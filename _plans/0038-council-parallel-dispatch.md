# 0038 — Council: Parallel Councillor Dispatch

**Date**: 2026-04-18
**Status**: Design
**Parent**: 0037

## Problem

`Council._run_round()` queries councillors sequentially. With 3 councillors averaging
~4s each, a single round costs ~12s. In debate mode with 3 rounds that's ~36s per plan
review. A session with 5 plan reviews burns ~3 minutes in council time alone.

Within any round, councillors are fully independent — each sees the same input (plus the
same prior round outputs in debate mode). There is zero data dependency between them
within a round. Sequential dispatch is pure waste.

## Fix

Replace the `for councillor in councillors` loop in `_run_round()` with parallel dispatch
using `concurrent.futures.ThreadPoolExecutor`.

**Why threads, not asyncio:**
- Provider calls are HTTP requests — I/O bound, GIL released during network wait
- Threads require no changes to provider interfaces (no async/await rewrites)
- `ThreadPoolExecutor` is stdlib, no new dependencies
- Simpler error handling than asyncio for a mixed sync codebase

## Latency model

```
                  Sequential              Parallel
Independent       N × avg_call            max(calls)       ← ~4-5s flat
Debate (3 rounds) rounds × N × avg_call   rounds × max     ← ~15s vs ~36s
```

For 3 councillors at ~4s avg:
- Independent sequential: ~12s → parallel: ~4s   (3x faster)
- Debate sequential:      ~36s → parallel: ~15s  (2.4x faster)

## Implementation

### `Council._run_round()` — parallel path

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _query_councillor(councillor, prompt, system, prior_rounds):
    """Called in a thread. Returns CouncillorDecision."""
    ...

def _run_round(self, council_input, councillors, round_number, prior_rounds):
    logger.info(f"  {council_header_tag()} ── Round {round_number} ...")
    prompt = self.adapter.build_prompt(council_input, prior_rounds)
    if prior_rounds:
        prompt += self.adapter.format_prior_rounds(prior_rounds)

    decisions = [None] * len(councillors)  # preserve order

    with ThreadPoolExecutor(max_workers=len(councillors)) as pool:
        futures = {
            pool.submit(self._query_one, councillor, prompt): i
            for i, councillor in enumerate(councillors)
        }
        for future in as_completed(futures):
            idx = futures[future]
            decisions[idx] = future.result()

    converged = self.adapter.decisions_converged([d.parsed for d in decisions])
    ...
```

### `Council._query_one()` — isolated per-councillor call

Extracts the single-councillor logic from `_run_round` into its own method so it can
be submitted to the thread pool cleanly.

```python
def _query_one(self, councillor, prompt) -> CouncillorDecision:
    provider = get_provider(councillor.provider, councillor.model)
    messenger = Messenger()
    messenger.add_user_message(prompt)
    response = provider.chat(messages=messenger.get_messages(), tools=[], system=self.adapter.system_prompt())
    raw = next((b.text for b in response.content if isinstance(b, TextBlock)), "")
    logger.info(f"  {council_tag(councillor.label)} response:\n{raw}")
    parsed = self.adapter.parse_response(raw)
    summary = self.adapter.summarize_decision(parsed)
    logger.info(f"  {council_tag(councillor.label)} decision: {summary}")
    return CouncillorDecision(
        label=councillor.label,
        provider=councillor.provider,
        model=councillor.model,
        raw_response=raw,
        parsed=parsed,
        round_number=...,
    )
```

### Config — add `max_workers`

```yaml
council:
  max_workers: null   # null = len(councillors); set to 1 to force sequential (debug)
```

`null` means "use as many workers as there are councillors" — always fully parallel.
Setting to `1` restores sequential behavior for debugging.

### Default mode

Change default `mode` back to `independent` in `config.yml`. Debate mode remains
available but is opt-in. With parallelization, independent mode is ~4-5s per review
regardless of councillor count.

## Error handling

If a councillor call throws (network error, API error), the thread raises and
`future.result()` re-raises in the main thread. Options:

- **Fail fast**: let the exception propagate — council run fails, plan goes through
  unchallenged (same behavior as today if a provider is down)
- **Degrade gracefully**: catch per-future exceptions, log a warning, exclude that
  councillor from synthesis — council continues with N-1 decisions

Recommendation: **degrade gracefully**. A single provider outage shouldn't block the
whole plan. If all councillors fail, fall through to approved (same as today).

## Log ordering

Parallel threads will produce log lines in non-deterministic order. Each line already
carries the councillor label (`[council][claude]`, `[council][gpt]`, etc.) so logs
remain readable — you can grep by label. The `CouncillorDecision` list is assembled
in councillor-config order (by index, not completion order) so metrics are stable.

## What doesn't change

- `_deliberate_debate()` loop: rounds are still sequential (Round 2 depends on Round 1
  output). Only dispatch *within* a round is parallelized.
- Synthesis, metrics, convergence check — unchanged.
- Debate mode still works, just each round is faster.

## Files

- `src/runtime/council.py` — `_run_round()`, `_query_one()`, `ThreadPoolExecutor`
- `src/config.py` — `CouncilConfig.max_workers: int | None`
- `config.yml` — `max_workers: null`, `mode: independent`
