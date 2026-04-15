# 0017 — Plan-and-Execute: Phase 2 — Spinner

## Goal

Give the user visible feedback during blocking operations. A terminal spinner
with concise status messages replaces the silent wait between input and response.

---

## Files

### New: `src/ui/__init__.py`
Empty package init.

### New: `src/ui/spinner.py`

A `Spinner` class using `threading.Thread` + `itertools.cycle`. No new
dependencies — stdlib only.

**Interface:**
```python
spinner.start(message)   # begin spinning with initial message
spinner.update(message)  # change message without stopping
spinner.stop()           # clear line and stop thread
```

**Spinner messages by stage:**

| Stage | Message |
|---|---|
| Direct execution (no plan) | `Thinking...` |
| Planning | `Planning...` |
| Executing step N of M | `Step N/M — <description>` (capped at ~40 chars) |
| Synthesizing | `Synthesizing response...` |

**Suppression:** spinner is suppressed when `--verbose` is active — log lines
streaming to stdout would interfere with spinner output.

---

## Notes

- Spinner runs on a daemon thread — process exit always cleans it up
- `stop()` clears the spinner line with spaces before returning so the
  Agent response prints cleanly on a blank line
- The spinner instance is created in `agent.py` and passed in or accessed
  via the call signature — not a global
