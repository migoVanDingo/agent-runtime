# 0037c — Council: Phase 3 — Color Logger

**Date**: 2026-04-18
**Status**: Implemented
**Parent**: 0037

## Changes

### `src/logger.py`

**ANSI color palette:**
| Source        | Color          | Code       |
|---------------|----------------|------------|
| User          | Bright cyan    | `\033[96m` |
| Assistant     | Bright green   | `\033[92m` |
| Runtime/dim   | Dim            | `\033[2m`  |
| Error/block   | Bright red     | `\033[91m` |
| Council HDR   | Bright yellow  | `\033[93m` |
| Synthesis     | Bold           | `\033[1m`  |
| Escalate      | Yellow         | `\033[33m` |
| Councillors   | Rotating palette (blue→magenta→yellow→cyan→green) by label |

**Per-councillor colors** — `get_councillor_color(label)`:
- Assigned by order of first encounter, consistent for the process lifetime
- 5-color palette, wraps if more than 5 councillors

**Tag helpers** (return colored prefix strings for log messages):
- `council_tag(label)` → `[council][<label>]` in yellow + councillor color
- `council_header_tag()` → `[council]` in bright yellow
- `synth_tag()` → `[synth]` in bold
- `user_tag()` → `[user]` in bright cyan
- `assistant_tag()` → `[assistant]` in bright green
- `escalate_tag()` → `[escalate]` in yellow

All tag helpers return plain ASCII tags when not a TTY or when `NO_COLOR` is set.

**`_is_tty()`**: `sys.stdout.isatty() and not os.environ.get("NO_COLOR")`

**`ColoredFormatter`**: subclass of `logging.Formatter` — colorizes the level name only.
Message content coloring is left to callers via the tag helpers.

**Dual handlers in `configure_logging()`**:
- File handler → plain `Formatter` (no ANSI in log files)
- Stream handler (verbose mode) → `ColoredFormatter` when TTY, plain otherwise

**`configure_logging()` also initializes `CouncilMetricsWriter`** via `init_metrics_writer(session_id)` — wired here so the metrics writer is ready before any council runs.

### `src/runtime/council.py`

All council log lines updated to use tag helpers:
- `council_header_tag()` for council-level events
- `council_tag(label)` for per-councillor events
- `synth_tag()` for synthesis trace and final verdict
