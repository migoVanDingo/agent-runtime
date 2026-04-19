# 0038a — Council: Parallel Dispatch + Log Cleanup

**Date**: 2026-04-18
**Status**: Implemented
**Parent**: 0038

## Changes

### `src/runtime/council.py` — parallel `_run_round()`

Replaced sequential `for councillor in councillors` loop with `ThreadPoolExecutor`.

**`_run_round()`**:
- Builds the shared prompt once (all councillors in a round see the same input)
- Submits one `_query_one()` future per councillor
- Collects results via `as_completed()`, preserving config order by index
- Graceful degradation: if a councillor call throws, logs a warning and substitutes
  an approved decision — council continues with N-1 real decisions rather than failing

**`_query_one(councillor, prompt, round_number)`** (new method):
- Isolated per-councillor logic — safe to run in a thread
- Creates its own `Messenger` and `provider` instance (no shared state)
- Logs label, raw response, and decision summary

**`n_workers = config.max_workers or len(councillors)`**:
- `null` in config → fully parallel (one thread per councillor)
- `1` → sequential, for debugging

### Latency improvement

```
                  Before (sequential)     After (parallel)
Independent       N × avg_call            max(calls)          ← ~4-5s flat
Debate (3 rounds) rounds × N × avg_call   rounds × max(calls) ← ~15s vs ~36s
```

### `src/config.py` — `CouncilConfig.max_workers: int | None`

Default `None` = fully parallel.

### `config.yml`

- `max_workers: null` added
- `mode: independent` (reverted from `debate` — debate is opt-in)
- `consensus_threshold: 0.60` (carried from bug fix)

### `src/logger.py` — `_StripANSIFilter`

ANSI escape codes were being embedded in log message strings by `council_tag()` etc.,
which caused them to appear raw (`[93m`, `[0m`) in the log file while rendering as
colors in the terminal.

Fix: `_StripANSIFilter` strips `\x1b[...m` sequences from `record.msg` before the
record is written. Applied to the file handler only — stream handler (terminal) keeps
colors.

```python
class _StripANSIFilter(logging.Filter):
    _ansi_re = re.compile(r"\x1b\[[0-9;]*[mGKHF]")
    def filter(self, record):
        record.msg = self._ansi_re.sub("", str(record.msg))
        return True
```

Log file now shows clean readable tags:
```
[council] independent — councillors: ['claude', 'gpt', 'grok']
[council] ── Round 1 ────────────────────────────────────────
[council][claude] querying anthropic...
[council][claude] response: ...
[council][claude] decision: {...}
[synth] synthesis:
[synth] step 2: 2/3 challenged (67% ≥ threshold 60%) → keep drop
[synth] final: {"verdict": "challenged", ...}
```

### `src/agent.py` — `── Plan (N steps) ──` banner

Added a clear plan banner between entity critic and plan validation:

```
── Entity critic ─────────────────────────────────────────
  no corrections needed
── Plan (3 steps) ────────────────────────────────────────
  Step 1 [analysis] tool=file_info: ...
  Step 2 [analysis] tool=strings: ...
  Step 3 [file_io] tool=write_file: ...
── Plan validation ───────────────────────────────────────
```

Previously the steps were logged with no banner, buried between entity critic and
validation banners.
