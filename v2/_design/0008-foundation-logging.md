# 0008 — Human-readable session logging

**Status:** complete
**Phase:** 2.3 (post-foundation polish)
**Implements:** v1-style session.log per session, so a human can read what
the agent did without parsing JSON.

## 1. Goals

1. **Per-session `session.log` file** alongside `events.jsonl` in the
   session directory. Same shape, same lifecycle, same source of truth
   (the event bus).
2. **v1-format lines** that scan cleanly:
   ```
   2026-05-19 12:00:00,123 [INFO] arc.runtime:   user: hey there
   2026-05-19 12:00:01,000 [INFO] arc.runtime:   → ls(path='.')
   2026-05-19 12:00:01,002 [INFO] arc.runtime:   ← ls (5 entries, 47 chars)
   ```
3. **Banners** for major boundaries (session start/end, turn N, etc.) so
   you can scan a long log and jump to interesting points.
4. **No new code in the runtime** — logging is a plugin that consumes
   events. Drop the plugin and you get no log; nothing else breaks.

## 2. Non-goals

- **No structured logging.** The structured representation is `events.jsonl`.
  `session.log` is purely for human eyes.
- **No log rotation.** One file per session; sessions are bounded; no rotation
  needed. (Global log across sessions is a separate future feature.)
- **No console output by default.** The TUI already renders the agent's
  activity. Console logging would duplicate it. Opt-in via config.
- **No reaching into Python's root logger.** This is plugin-internal; don't
  pollute the global logger state.

## 3. Design decisions

### A. Plugin, not core

The log_writer is just another `on_event` subscriber. Same shape as the
JSONL recorder. This keeps v2's "everything is a plugin" invariant
intact — if a user wanted to replace it with a different format
(syslog, structured JSON to stderr, whatever), they'd write their own.

### B. Use stdlib `logging` internally, with a per-session FileHandler

The plugin instantiates its own `logging.Logger` per session, attaches
a `FileHandler` pointed at `session.log`, and uses standard formatters.
This gives us correct timestamps + thread-safety + flush behavior for
free without reinventing them.

The logger is **not** registered with the root logger — it's a
plugin-local instance so it can't accidentally affect other libraries
or pytest output.

### C. One log line per event, mostly

Each event maps to one log line. Banners (session boundary, turn
boundary) are 2-3 lines: the banner separator + a line of context.
We don't log every internal hook firing — that's noise.

### D. Format string matches v1

```
%(asctime)s [%(levelname)s] %(name)s: %(message)s
```

with `datefmt = "%Y-%m-%d %H:%M:%S"`. Adds milliseconds via `%(msecs)03d`
in the asctime. Logger name will be `arc.runtime`, `arc.tool`, `arc.llm`,
etc. — namespaced so a user can grep by category.

### E. Message body conventions

| Event | Format |
|-------|--------|
| `session.started` | banner + `provider: X / Y`, `tools: [...]`, `home: ...` |
| `session.ended` | banner + `n_messages: N` |
| `turn.started` | banner `── Turn N ──` + `user: <input>` |
| `turn.ended` | `Turn N complete (success=T/F, X llm calls, Y tool calls)` |
| `llm.call.started` | `→ llm.call (model=X, N msgs, M tools)` |
| `llm.call.completed` | `← llm.call (stop=X, tokens=in/out)` + optional text preview |
| `llm.call.failed` | `✖ llm.call failed: <exception>` at ERROR level |
| `tool.call.started` | `→ tool: name(input-preview)` |
| `tool.call.completed` | `← tool: name → <output-preview or summary>` |
| `tool.call.failed` | `✖ tool: name → <error>` at ERROR |
| `tool.call.denied` | `⊘ tool: name denied (<reason>)` at WARN |
| `runtime.cycle_detected` | `⚠ cycle detected (3 identical calls to X) → forcing wrap-up` at WARN |
| `plugin.hook.failed` | `plugin <name> failed in hook <hook>: <exc>` at WARN |
| `plugin.disabled` | `plugin <name> disabled (exceeded failure threshold)` at WARN |
| `pause.requested` | `pause requested` at INFO |

Long inputs/outputs get truncated to a configurable preview length
(default 200 chars). The full thing is in `events.jsonl`.

### F. Config keys

Under `plugins.enabled[].config` for the log-writer entry:

```yaml
- name: log-writer
  config:
    level: info               # debug | info | warn | error
    preview_chars: 200        # truncate long messages/outputs in the log
    include_events: []        # if non-empty, ONLY log these event types
    exclude_events: []        # event types to skip
    include_thinking: false   # render <thinking> blocks at DEBUG level
  hooks_order:
    on_session_start: 5       # before recorder so file exists when events fire
    on_event: 50
    on_session_end: 5
```

Defaults: log everything at INFO, truncate at 200 chars.

## 4. File layout

```
<ARC_HOME>/sessions/<session_id>/
  events.jsonl         # canonical (machine)
  session.log          # human-readable (new)
  meta.json
  config.snapshot.yml
```

## 5. New files

```
src/arc/plugins/log_writer/
  __init__.py
  plugin.py            # LogWriterPlugin
  formatter.py         # event → log message (pure functions)
```

Updates:
- `arc/plugins/__init__.py` — register the builder
- `arc/defaults.py`         — enable the plugin
- `arc/cli.py`              — `arc log <session_id>` subcommand (phase C)

## 6. Phase split

| Phase | Scope |
|-------|-------|
| **A** (task #89) | Plugin + formatter for all event types. Hard-coded defaults. Acceptance test that asserts log file exists + contains expected lines after a run. |
| **B** (task #90) | Config knobs (level, preview_chars, include/exclude). Defaults.py entry. |
| **C** (task #91) | `arc log <id>` CLI subcommand to print the log. Tail/follow support deferred. Integration test against real Gemini. |

## 7. Open questions (resolved with defaults; can override if you disagree in the morning)

1. **One file per session vs append to a single rolling log?** — One per
   session. Matches v1, matches v2's session-oriented design. A future
   `~/.arc/arc.log` global log can be a separate plugin.
2. **Should the log capture full LLM message content?** — Truncated at
   `preview_chars`. The full bytes are in `events.jsonl`. The log is for
   scanning, not for archival.
3. **Console output too?** — No by default. Opt-in flag deferred. The
   TUI already shows live activity; duplicating it would just clutter
   the terminal.
4. **Logger naming convention?** — `arc.runtime` for loop/session events,
   `arc.tool` for tool events, `arc.llm` for LLM events, `arc.plugin`
   for plugin events. One namespace per concern so `grep` is useful.

## 8. Implementation notes

### 8.1 What landed

| Task | File(s) | Status |
|------|---------|--------|
| #88 Design doc | this file | ✅ |
| #89 Plugin + formatter | `arc/plugins/log_writer/{plugin.py, formatter.py}` | ✅ |
| #90 Config + filters | `arc/defaults.py` (log-writer entry), `arc/plugins/__init__.py` (builder) | ✅ |
| #91 `arc log` CLI + acceptance test | `arc/cli.py`, `tests/integration/test_logging_acceptance.py` | ✅ |

**Test coverage:** 30 unit tests (formatter + plugin) + 8 acceptance tests
against real Gemini. **302 tests total, all green.**

### 8.2 Sample output

```
2026-05-20 17:07:52.972 [INFO] arc.runtime: ========================================================
2026-05-20 17:07:52.972 [INFO] arc.runtime:   Session started
2026-05-20 17:07:52.972 [INFO] arc.runtime:   session_id: SES01KS3KES0AEQ5NP3YG57C8N48R
2026-05-20 17:07:52.972 [INFO] arc.runtime:   provider:   gemini / gemini-3.1-flash-lite-preview
2026-05-20 17:07:52.972 [INFO] arc.runtime:   tools:      ls, bash_exec
2026-05-20 17:07:52.972 [INFO] arc.runtime: ========================================================
2026-05-20 17:07:52.972 [INFO] arc.runtime: ── Turn (TRN01KS3KES0C2WZEH7F67MPY4XDX) ────────────────
2026-05-20 17:07:52.972 [INFO] arc.runtime:   user: list /tmp
2026-05-20 17:07:52.972 [INFO] arc.llm:   → llm.call  (gemini-3.1-flash-lite-preview, 1 msgs, 2 tools)
2026-05-20 17:07:53.652 [INFO] arc.llm:   ← llm.call  (stop=tool_use, tokens=396/15)
2026-05-20 17:07:53.652 [INFO] arc.tool:   → ls(path='/tmp')
2026-05-20 17:07:53.656 [INFO] arc.tool:   ← ls (58 lines, 1834 chars)
2026-05-20 17:07:53.656 [INFO] arc.tool:     05391F0B-8E85-4C94-B50E-843907585AF7_IN\n... [+1634 chars]
2026-05-20 17:07:58.008 [INFO] arc.runtime:   assistant: The contents of `/tmp` are: ... [+1895 chars]
2026-05-20 17:07:58.008 [INFO] arc.runtime:   turn complete  (2 llm, 1 tool)
2026-05-20 17:07:58.009 [INFO] arc.runtime: ========================================================
2026-05-20 17:07:58.009 [INFO] arc.runtime:   Session ended (4 messages)
2026-05-20 17:07:58.009 [INFO] arc.runtime: ========================================================
```

### 8.3 Decisions made during implementation

**Display-name pattern via `extra={"display_name": ...}`.** Initial sketch
tried per-category loggers (`arc.runtime`, `arc.llm`, etc.) directly. That
risked cross-session pollution if multiple sessions ran in one process
(the loggers are global state). Final design: a per-session logger named
`arc._sess.<session_id>`, with `propagate=False` to keep records out of
root, and a custom `_DisplayNameFormatter` that swaps in `display_name`
from the LogRecord's `extra` dict. So records APPEAR to come from
`arc.runtime` etc. while actually living in the per-session namespace.
Best of both worlds.

**Banner separators duplicated as identical lines (the `=` strings).**
Each banner is its own LOG line so timestamps stay accurate. Means a
session.started block is ~7 lines tall. Worth the readability.

**No `logging.basicConfig` calls.** The plugin scrupulously avoids
touching the root logger. Tests verify this: `test_plugin_does_not_pollute_root_logger`.

### 8.4 Existing sessions don't have logs

The log-writer plugin only runs for new sessions after its config entry
landed. Old sessions in `~/.arc/sessions/SES*/` don't have `session.log`.
This is expected — there's no retroactive logging. `arc log <old_session_id>`
prints a clear error.

### 8.5 Operational state

| Thing | State |
|-------|-------|
| `session.log` auto-created per session | ✅ |
| Banners + categorized logger names | ✅ |
| Truncation at `preview_chars` (default 200) | ✅ |
| Config filters (include/exclude/level) | ✅ |
| `arc log <id>` CLI | ✅ |
| `arc log <id> --tail N` | ✅ |
| Plugin failure isolated from runtime | ✅ (registry catches) |
| Records don't leak to root logger | ✅ |

### 8.6 Note for the user

Anyone who bootstrapped before this phase needs to run `arc bootstrap --force`
once to pick up the `log-writer` entry in their config. Sessions started
before that won't have logs. After the refresh, every new session gets one.

## 9. Lessons

1. **Plugins for "view" concerns work the same way as plugins for "data"
   concerns.** The JSONL recorder writes the structured view; log_writer
   writes the human view. Same hooks, same lifecycle. The architecture
   composes cleanly.

2. **`extra={...}` on LogRecord is underused.** Most projects fight Python's
   logging by inventing custom adapters. The simple `extra` dict + a
   formatter that looks at it solves the categorization problem without
   any of that.

3. **Defaults matter more than features.** The log-writer is enabled by
   default at INFO level. That choice means a user gets the full v1-style
   log experience without ever touching config. Reading the config to
   enable it would be a friction point that some users never crossed.

## 10. What's next

This closes the "polish v1-grade observability" item. The runtime now
has BOTH structured (`events.jsonl`) and human (`session.log`) views of
every session, plus replay/branch/rerun/pause-resume on top.

Next steps (your choice for the morning):
- Capability plugins: context manager, sub-agents, monitor — each as a
  separate add-on per the design split
- TUI polish: clearer turn boundaries, theme support, the `/sessions`
  slash command
- Documentation pass: the `_architecture/` folder is still empty; an
  overview doc explaining the runtime to a newcomer would help here.

