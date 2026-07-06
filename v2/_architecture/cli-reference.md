# CLI reference + operations

Every subcommand, flag, and operational concern (sessions directory layout,
events schema, replay semantics) in one place.

---

## Global flags

| Flag | Meaning |
|---|---|
| `--home <path>` | Override `ARC_HOME` for this invocation. Useful for sandboxed testing or per-project arc installs. |
| `--version` | Print arc version and exit. |

---

## Commands

### `arc`

Start the interactive TUI. With no subcommand, this is the default.

- Loads `$ARC_HOME/config.yml`, builds providers/tools/plugins, drops into
  prompt_toolkit input loop.
- Slash commands: `/help`, `/exit`, `/quit`, `/clear`, `/sessions`,
  `/replay`, and the time-travel set (0026): `/rewind`, `/retry`, `/model`,
  `/tab`.
- Tab-complete on slash commands. ↑/↓ recalls input history.
- Bottom toolbar shows `provider/model · SES… · turn N · in→out (total) · $cost`
  when `tui.toolbar_enabled` is true and pricing data is available. Grows a
  `tabs 1* 2 …` segment once a branch opens a second tab.

**Time travel (0026).** Fork the conversation without leaving the TUI:

- `/rewind` — walk turns with ←/→ (each step prints the turn you land on),
  Enter arms a branch at that turn; `/rewind N` arms directly. The next
  prompt you submit forks a new session seeded with turns `1..N` (empty
  input cancels — branch-on-submit, nothing is created until you commit).
- `/retry` — re-ask the last prompt verbatim on a fresh branch.
- `/model X` / `/model prov/X` — continue this conversation on another
  model. Session-scoped (config.yml untouched); the branched session's
  `config.snapshot.yml` records the effective provider so replay stays
  correct.
- `/tab [N]` — list/switch tabs (also alt+1…9). Branches open in a new tab;
  the parent stays live. `/exit` closes the focused tab (falling back to the
  survivor), `/quit` or the last `/exit` ends everything. `tui.tabs_max`
  (default 4) caps live tabs.

Every branch stamps lineage into the child's `meta.json` (`resumed_from`,
`branched_at_turn`, `restored_message_count`, plus `retry_of_turn` /
`provider_override` when applicable) and emits `session.branched`
(+ `provider.swapped` on a model swap) — the latter is the authoritative
record. The original session stays recorded and resumable.

### `arc bootstrap [--force]`

Create `$ARC_HOME/` and write the default `config.yml`. Refuses to overwrite
unless `--force`.

- Creates `$ARC_HOME/sessions/` (with empty `index.jsonl`).
- Writes `config.yml` from [`src/arc/defaults.py`](../src/arc/defaults.py).
- `--force` overwrites the config in place. Useful after upgrading arc and
  wanting the new defaults. Back up first if you've customized.

### `arc run "<prompt>"`

One-shot, non-interactive turn. Prints the assistant's final text to stdout
and exits.

- No TUI, no slash commands. Headless — `UserGate` is `NoOpGate` (escalation
  patterns auto-deny).
- Still records to `$ARC_HOME/sessions/<sid>/`. You can `arc replay` it.
- Exit code: 0 on success, 1 on any tool/LLM failure.

### `arc sessions`

List recorded sessions from `$ARC_HOME/sessions/index.jsonl`.

- Columns: `session_id`, `started_at`, `provider/model`, chain markers
  (`resumed_from`, `replay_of`, `rerun_of`, `branched_at_turn`).

### `arc timeline [--open] [--rebuild]`

Generate / open the **visual session timeline** (0027) — the session forest
rendered as a self-contained static HTML page in the sessions dir.

- No args: ensure `sessions/timeline.html` is current (generate if missing),
  print its path.
- `--open`: also open it in a browser (`webbrowser.open`).
- `--rebuild`: force full regeneration, including every per-session
  `session.html` and node cache (recovery after a format change).
- Normally you don't run this: the `timeline` builtin plugin regenerates the
  page on every session end. Lanes = sessions, nodes = turns, fork edges drop
  from a parent turn to the child (branch/retry solid, resume/replay/rerun
  dashed). Click a node → detail panel with a copyable `/rewind` command;
  each node links to `<sid>/session.html`. Files live at
  `sessions/timeline.html` + `sessions/<sid>/session.html`.

### `arc show <id>`

Pretty-print every event in `<sid>/events.jsonl`. Useful for debugging
plugin behavior or verifying event ordering.

- Output is structured (event name, payload, parent chain).
- Pipe through `less` for large sessions.

### `arc log <id> [--tail N]`

Print `<sid>/session.log` (the human-readable log).

- `--tail N` prints only the last N lines.
- Mirrors what would have scrolled past in the TUI: tool calls, assistant
  text, errors. Tokens per LLM call. Per-turn footer.

### `arc config show` / `arc config path`

Inspect the resolved configuration.

- `show` dumps the loaded `Config` (after defaults + overrides).
- `path` prints the file arc loaded. Handy for `vim $(arc config path)`.

### `arc replay <id> [--live-llm] [--strict|--lenient]`

Replay a recorded session. Two modes:

| Mode | Flags | LLM | Tools | Use for |
|---|---|---|---|---|
| 2 — Deterministic | (default) | stubbed from recording | stubbed from recording | Verify byte-faithful reproduction. |
| 3 — Test prompt change | `--live-llm` | real | stubbed | See if a prompt/model change breaks the scenario. |

- `--strict` (default): any event divergence fails the replay.
- `--lenient`: tolerate token/timestamp differences. Useful for cross-day
  replay when prices/timestamps shift.

Replay creates a new session with `replay_of: <original_id>` in `meta.json`.

### `arc resume <id> [--prompt "..."] [--at-turn N]`

Continue a recorded session. Two modes:

| Mode | Flags | Behavior |
|---|---|---|
| 1 — Time-travel | `--prompt "..."` | Append a new user turn; resume from the end of the original. |
| 4 — Branch | `--at-turn N --prompt "..."` | Fork after turn N, append the new prompt as turn N+1. Original is unchanged. |

Resume creates a new session with `resumed_from` (or `branched_at_turn`)
in `meta.json`.

### `arc rerun <id> [--stop-on-error]`

Mode 5: extract every user input from the original recording, feed them
through a fresh agent. Regression test for prompt/tool changes.

- `--stop-on-error`: halt on first failure instead of continuing.
- New session gets `rerun_of: <original_id>` in `meta.json`.

---

## `ARC_HOME` resolution order

1. `--home <path>` flag
2. `ARC_HOME` env var
3. `./.arc/` (in cwd, if it exists)
4. `~/.arc/`

The third rule enables per-project arc installs — drop a `.arc/` next to
your repo's root and arc uses it automatically when run from that tree.

---

## Sessions directory layout

```
$ARC_HOME/                                    default: ~/.arc/
  config.yml                                  the loaded config
  history                                     prompt_toolkit input history (one line per submitted prompt)
  pricing_cache.json                          LiteLLM pricing snapshot (refreshed weekly)
  sessions/
    index.jsonl                               one line per session (id, times, provider, model)
    <session_id>/
      events.jsonl                            canonical event log (byte-faithful)
      session.log                             human-readable log
      meta.json                               metadata + chain markers
      config.snapshot.yml                     config as of session start (replay uses this)
      pause                                   signal file — `touch` to pause the running agent
```

Each session is self-contained. No shared databases, no rolling logs.
Delete a session directory and nothing else breaks.

---

## `meta.json` shape

```json
{
  "session_id": "SES01...",
  "started_at": "2026-05-22T00:00:00+00:00",
  "ended_at": "2026-05-22T00:15:00+00:00",
  "provider": "anthropic",
  "model": "claude-haiku-4-5",
  "workspace": ".",
  "last_outcome": {
    "success": true,
    "n_tool_calls": 4,
    "n_llm_calls": 5,
    "error": null
  },
  "resumed_from": "SES01...",
  "replay_of": "SES01...",
  "rerun_of": "SES01...",
  "branched_at_turn": 3
}
```

Chain markers are present only when applicable. `arc sessions` reads them
to draw the lineage.

---

## Event taxonomy

Every event in `events.jsonl` is a `RuntimeEvent` with this shape:

```json
{
  "id": "EVT01...",
  "type": "tool.call.completed",
  "scope": "turn",
  "session_id": "SES01...",
  "turn_id": "TRN01...",
  "parent_event_id": "EVT01...",
  "timestamp": "2026-05-22T00:00:01.234567+00:00",
  "payload": { ... type-specific ... }
}
```

### Event types

| Type | Fires when |
|---|---|
| `session.started` | Session begins. Payload: provider, model, workspace, config snapshot. |
| `session.ended` | Session ends. Payload: outcome summary. |
| `turn.started` | User turn begins. Payload: user input. |
| `turn.ended` | Turn ends. Payload: outcome (n_tool_calls, n_llm_calls, success). |
| `llm.call.started` | Before provider.chat(). Payload: canonical request (messages, system, tools, params). |
| `llm.call.completed` | After provider.chat(). Payload: response blocks, stop_reason, tokens, raw provider response. |
| `llm.call.failed` | provider.chat() raised. Payload: error message + class. |
| `tool.call.started` | Before tool.execute(). Payload: tool name, input. |
| `tool.call.completed` | After tool.execute(). Payload: output, ok, error_code. |
| `tool.call.failed` | tool.execute() raised non-`ToolError`. Payload: error. |
| `tool.call.denied` | `before_tool_call` returned `ToolDenial`. Payload: tool name, reason. |
| `hook.fired` | Any hook invocation. Payload: hook name, plugin name, returned None/transformed. |
| `plugin.hook.failed` | Plugin raised inside a hook. Payload: plugin name, exception. |
| `plugin.disabled` | Plugin quarantined after exceeding `failure_threshold`. Payload: plugin name. |
| `pause.checkpoint.passed` | `pause_check` ran with no pause. Payload: checkpoint id. |
| `pause.requested` | `pause_check` raised `PauseRequested`. Payload: source (signal file, in-process flag). |
| `pause.resumed` | Resume started after a pause. |
| `runtime.cycle_detected` | Cycle detector triggered. Payload: tool name, repeated input. |
| `runtime.context_packed` | Context manager filtered messages. Payload: counts before/after, fragments dropped. |
| `runtime.conversation_cleared` | User ran `/clear` in the TUI. Payload: number of messages cleared. |
| `session.branched` | TUI branched via `/rewind` or `/retry` (0026). Emitted in the NEW session. Payload: `source_session_id`, `branched_at_turn`, `restored_message_count`, optional `retry_of_turn`. Authoritative lineage record. |
| `provider.swapped` | `/model` changed the provider mid-conversation (0026). Payload: `from_provider`, `from_model`, `to_provider`, `to_model`. |

Causation chains: every event has a `parent_event_id` pointing at the event
that caused it (`tool.call.started` is parented to the `llm.call.completed`
that produced its tool_use block, etc.).

---

## Replay semantics

### Byte-faithful (mode 2)

The replay engine reads `events.jsonl`, stubs the provider with a
`ReplayProvider` that returns the recorded `LLMResponse` for each call, and
stubs each tool with a `ReplayToolRegistry` that returns the recorded
`ToolResult`. The runtime re-emits events as it goes; the engine asserts
they match the recording, in order, byte-for-byte.

If they don't, you get a structured diff: which event diverged, what was
expected, what was emitted. See `arc/replay/diff.py`.

### Live-LLM (mode 3)

Same as above, but the provider is the real one. The recorded request is
replayed verbatim (same messages, same system, same params), so any
divergence in the response is *the model's*, not yours. Use this to test
whether a prompt or model change still produces a working scenario.

### What replay can't do

- **Workspace state isn't snapshotted.** If your scenario depends on files
  written during turn 1, those files are not restored before turn 1 of the
  replay. Either snapshot the workspace yourself or write tools that are
  idempotent.
- **Real-world side effects re-fire.** If `bash_exec` ran `rm -rf` in the
  original session, deterministic-mode replay does *not* re-run it (because
  tools are stubbed) — but live-mode replay would re-issue the LLM request
  that produced the tool_use, and if you go further with live tools, side
  effects re-execute.

---

## Headless vs interactive behavior

A few things change based on whether arc is interactive:

| | Interactive (`arc`) | Headless (`arc run`, `arc replay`) |
|---|---|---|
| `UserGate` | `TUIGate` (prompts y/N) | `NoOpGate` (auto-denies escalation) |
| Slash commands | Yes | N/A |
| Bottom toolbar | Yes (if enabled) | N/A |
| Input history | Yes | N/A |
| Output rendering | Rich Markdown + spinners | Plain text |

This means **safety patterns under `guard.escalation_required_patterns` are
auto-denied in headless mode**. If you want headless to allow them, either
move the pattern to `allowlist_tools` or run interactively.
