# 0011 — TUI polish v1 (10 items)

**Status:** in progress
**Phase:** 3.2
**Implements:** all 10 items from the polish discussion

## 1. Scope

Ten focused improvements to the inline TUI. All independent, no refactoring
of the core. Goal: take the TUI from "works" to "scans well day-to-day."

| # | Item | Effort |
|---|------|--------|
| 1 | Input history (↑/↓ recall) | xs |
| 2 | `/clear` actually works | s |
| 3 | `/sessions` renders inline | s |
| 4 | Slash command tab completion | s |
| 5 | Turn separators + resumed-session banner | xs |
| 6 | Better `/help` (keybinds, env, config tips) | xs |
| 7 | Bottom toolbar (provider/model/session/turn/tokens/$) | m |
| 8 | Long tool output collapsing | s |
| 9 | Token cost estimation (LiteLLM data) | m |
| 10 | Thinking-block rendering (Anthropic 3.7+/4+) | s |

## 2. Decisions made (without escalation)

### A. Pricing data source: LiteLLM's community-maintained JSON

`https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`

- Community-maintained, covers Anthropic + Gemini + OpenAI + much more
- Maps `<model_name>` → `{input_cost_per_token, output_cost_per_token, ...}`
- Fetched on first use, cached at `<ARC_HOME>/pricing_cache.json`
- Cache refreshes weekly; if fetch fails, use stale cache; if no cache and fetch fails, fall back to "(unknown)"
- One stdlib HTTP call via `urllib.request`, no new deps
- Lookup tries multiple forms: `<provider>/<model>`, `<model>`, model name with date variants

If a future model isn't in LiteLLM yet, cost shows "(unknown)" — not the end
of the world, and the LiteLLM project usually catches up within days of a
new model release.

### B. Bottom toolbar layout

```
provider/model · SES01KS3... · turn N · 1240→340 tokens (5,300 total) · $0.0023
```

Components, left to right:
- `<provider>/<model>` — never changes mid-session
- `<session_id[:8]>...` — first 8 chars of session ID
- `turn N` — turn count in this session
- `<last_in>→<last_out>` then total in parens — most recent turn's token use + cumulative
- `$<cost>` — cumulative dollars based on tokens × pricing

Pricing column omitted entirely if LiteLLM lookup fails (rather than showing
"(unknown)" everywhere).

Updates between turns. Prompt_toolkit's `bottom_toolbar` evaluates a callable
on each prompt() invocation — so toolbar shows fresh stats after every turn
returns, but not live-during-turn. Live-during-turn would need an Application-
layer rewrite; out of scope for v1 polish.

### C. Thinking blocks: shown by default, dim italic

Config knob `tui.show_thinking: true` already exists. We honor it. Default
true so users see the model's reasoning. Rendered in `[dim italic]` style
so it visually subordinates to the assistant's actual response.

Anthropic `thinking` content blocks (Claude 3.7+/4+) translate to
`ContentBlock(type="thinking", text=...)` in our universal types. The
TUI renders them under a `◇ thinking` glyph above the assistant's text.

If `tui.show_thinking: false`, they're silently dropped from TUI render
(but still preserved in events.jsonl + session.log).

### D. `/clear` resets in place, emits `conversation.cleared` event

The agent session continues — same session_id, same events.jsonl — but
the in-memory conversation gets reset. The event log shows the reset
explicitly so audit trails reconstruct correctly.

Could have instead created a new session on `/clear`, but that breaks
the "one TUI invocation = one session" mental model. Reset-in-place is
cleaner and the event trail captures the gap.

### E. `/sessions` renders inline via Rich Table

Reads `<ARC_HOME>/sessions/index.jsonl`, builds a Rich Table with columns:
`session_id`, `started_at`, `provider`, `model`, `chain`. Chain column
shows resumed_from / branched_at / rerun_of markers if present in meta.

### F. Slash command tab completion

`prompt_toolkit.completion.WordCompleter` with the slash command list.
Trigger on Tab after `/`. Hint at available commands when user types `/`
alone.

## 3. Implementation plan

Order (each independent enough to ship/test before moving on):

1. **Slash commands** (3 of 10 done in one push — `/clear`, `/sessions`, `/help`)
2. **Turn separators + resumed-session banner**
3. **Long tool output collapsing**
4. **Pricing module** (separate file, well-tested, used by toolbar)
5. **Thinking-block translation** (provider) + **rendering** (TUI)
6. **Input history**
7. **Slash command tab completion**
8. **Bottom toolbar** (depends on pricing)
9. **Tests + live smoke**

## 4. New files

```
src/arc/tui/
  pricing.py            LiteLLM-backed pricing lookup + cost calc
  separators.py         Rich-based turn separators
```

Updates:
- `arc/tui/app.py` — most of the work
- `arc/tui/render.py` — separator + thinking-block rendering
- `arc/runtime/events.py` — `CONVERSATION_CLEARED` event type
- `arc/runtime/hooks.py` — `ContentBlock.type` adds "thinking"
- `arc/providers/anthropic.py` — translate `thinking` blocks
- `arc/plugins/log_writer/formatter.py` — formatter case for cleared event
- `arc/defaults.py` — new `tui.*` config keys

## 5. New config keys

```yaml
tui:
  show_thinking: true              # already exists; we'll honor it now
  tool_output_max_lines: 30        # new — collapse longer outputs
  toolbar_enabled: true            # new — bottom toolbar on/off
  input_history_enabled: true      # new — ↑/↓ recall via FileHistory
```

## 6. Implementation notes

All 10 items shipped in one pass. Notes by item:

### 6.1 Input history
- `prompt_toolkit.history.FileHistory` at `$ARC_HOME/history`. One file per
  install (sessions all share). Persists across runs.
- Path resolved by `TUIApp._resolve_history_path()` — walks plugins to find
  the `JSONLRecorder.sessions_dir.parent` (i.e. `$ARC_HOME`). If no recorder
  is wired, history is disabled silently.
- Gated by `tui.input_history_enabled` (default true).

### 6.2 /clear
- Wipes `session._messages` in place — keeps the same `session_id`, the
  same JSONL file, the same turn counters.
- Emits `EventType.CONVERSATION_CLEARED` carrying `{n_messages_cleared}`.
  The recorder writes it to the audit trail. The toolbar resets its
  per-session token totals + turn count.
- Documented in `/help`. Distinct from killing the session and starting
  fresh: clear is a soft reset that stays in the audit trail.

### 6.3 /sessions inline
- Reads `$ARC_HOME/sessions/index.jsonl` (the recorder writes one line per
  session at session start).
- For each row, opens `<sid>/meta.json` for chain markers (resumed_from,
  replay_of, rerun_of, branched_at_turn). Failures are skipped silently.
- Rich Table, no panel, dim header. Falls back to "no sessions recorded
  yet" or "could not locate sessions directory" when appropriate.

### 6.4 Slash command tab complete
- `prompt_toolkit.completion.WordCompleter` with the static slash command
  list. Tab inserts longest common prefix; second tab cycles.
- Match is case-insensitive, prefix-only (so typing `/cl<Tab>` completes
  to `/clear`).

### 6.5 Turn separators + resumed-session banner
- `render_turn_separator()` — dim 80-char horizontal line, printed after
  each completed turn (not after `/exit` / errors).
- `render_session_banner(..., resumed_from=...)` — when present, adds a
  "resumed   from <sid>" line in bold magenta in the info panel.
- Banner gets `resumed_from` from `<sid>/meta.json`, looked up at session
  start via `_read_resumed_from_meta()`.

### 6.6 Better /help
- Five sections now: slash commands, keybinds, env vars, config, more.
- Lists the new commands (`/clear`, `/sessions`) and the new config keys
  (`tui.show_thinking`, `tui.toolbar_enabled`).
- Tests updated to assert section headers, not the old "commands:" string.

### 6.7 Bottom toolbar
- `prompt_toolkit.bottom_toolbar` — single-line, updates between
  `prompt()` calls (prompt_toolkit doesn't push toolbar updates mid-input,
  which is fine for this use case).
- Segments: `provider/model · session · turn N · in/out tokens · $cost`.
- Cost segment hidden if pricing lookup returns None (e.g. offline,
  unknown model, or — on macOS — Python lacking a CA bundle and SSL
  verification failing).
- Gated by `tui.toolbar_enabled`.

### 6.8 Long tool output collapsing
- `render_tool_result(..., max_lines=N)` — shows head (5 lines) + elision
  marker + tail (5 lines) plus a summary `tool_name (N lines, M chars —
  collapsed)`.
- Defaults to `tui.tool_output_max_lines = 30`. Full output is preserved
  byte-for-byte in `events.jsonl` and `session.log`.
- Critical for Ghidra-class tools whose `decompile_function` output can be
  tens of thousands of chars.

### 6.9 Token cost via LiteLLM
- `arc/tui/pricing.py` — `PricingTable` with a JSON cache at
  `$ARC_HOME/pricing_cache.json`. Fetches LiteLLM's
  `model_prices_and_context_window.json` on first use; refreshes weekly.
- Stdlib `urllib.request` only — no new dependency.
- Graceful fallback chain: fresh cache → upstream fetch → stale cache →
  None. Cost segment in toolbar simply disappears when None.
- `format_cost()`: 4 decimals under $0.01, 3 under $1, 2 above. None →
  empty string.
- Operational caveat: on macOS, Python's stock SSL stack may not have a
  CA bundle, so the upstream fetch raises `URLError(SSL...)` and cost is
  hidden. Fix at the user level: `pip install --upgrade certifi` or use
  `python.org`'s installer's "Install Certificates.command".

### 6.10 Thinking blocks
- Provider layer: `arc/providers/anthropic.py` translates `type=thinking`
  response blocks into `ContentBlock(type='thinking', text=..., metadata=
  {'signature': ...})`. On follow-up turns the signature is echoed back
  via `_assistant_blocks` — Anthropic requires this for 3.7+/4+ models.
- TUI layer: `render_thinking()` renders dim italic under a `◇ thinking`
  glyph so it visually subordinates to actual assistant text.
- Gated by `tui.show_thinking` (default true).
- Other providers (Gemini) don't surface thinking blocks today; the
  rendering path is no-op for them.

## 7. State

Shipped. 362 tests pass (`pytest tests/ -q`), including 13 new tests for
PricingTable, 4 new TUI tests covering `/clear`, `/sessions`, thinking
blocks, and tool-output collapsing. Live smoke verifies config plumbing,
event taxonomy, rendering primitives, and graceful pricing fallback.
