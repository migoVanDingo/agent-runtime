# Configuration reference

Complete reference for every key in `$ARC_HOME/config.yml`. The canonical
default is in [`src/arc/defaults.py`](../src/arc/defaults.py) — `arc bootstrap`
writes it verbatim.

Principle: **no hardcoded user-tunables.** If a value is user-tunable, it
lives here. If you can't grep for the key in `config.yml`, the knob doesn't
exist.

---

## `runtime`

Controls the ReAct loop, caps, system prompt, cycle detection.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `workspace` | string | `"."` | Working directory the agent operates in. Relative paths in tools resolve here. |
| `max_iterations` | int | `50` | Hard cap on ReAct iterations per turn. After this, runtime injects `iteration_cap_message` and stops. |
| `max_tool_calls_per_turn` | int | `30` | Hard cap on tool invocations per turn. Triggers `tool_call_cap_message`. |
| `show_thinking` | bool | `true` | (Deprecated location — use `tui.show_thinking`.) |
| `log_level` | string | `"info"` | `debug` \| `info` \| `warn` \| `error`. Controls `arc.*` Python logger output. |
| `system_prompt` | string | (see defaults) | Base system prompt sent to the model. Plugins can extend via `before_llm_call`. |
| `iteration_cap_message` | string | (see defaults) | Injected as the next "user" message when `max_iterations` is hit. |
| `tool_call_cap_message` | string | (see defaults) | Injected when `max_tool_calls_per_turn` is hit. |
| `cycle_detection_threshold` | int | `3` | After N identical tool calls in a row, force a tool-less synthesis turn. |
| `cycle_detected_message` | string | (see defaults) | Injected when cycle is detected. |
| `plugin_failure_threshold` | int | `3` | After N exceptions, a plugin is quarantined for the session. (Lives under `plugins.failure_threshold`.) |

---

## `provider`

Selects and configures the LLM backend.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | `"gemini"` | `"gemini"` \| `"anthropic"`. Matches `_PROVIDERS` keys in `arc/providers/__init__.py`. |
| `model` | string | `"gemini-3.1-flash-lite-preview"` | Model id passed to the SDK. Provider-specific. |
| `api_key_env` | string | `"GEMINI_API_KEY"` | Env var the provider reads at construction. Switch to `"ANTHROPIC_API_KEY"` for Anthropic. |
| `base_url` | string\|null | `null` | Override SDK endpoint. `null` = SDK default. |
| `timeout_seconds` | int | `60` | Per-call HTTP timeout. |
| `retry.max_attempts` | int | `3` | Retry attempts on transient failures. |
| `retry.backoff_base_seconds` | int | `2` | Initial backoff. |
| `retry.backoff_max_seconds` | int | `32` | Backoff ceiling. Exponential with jitter. |
| `params.temperature` | float | `0` | Sampling temperature. |
| `params.max_tokens` | int | `4096` | Required by Anthropic. Recommended for Gemini. |
| `params.<other>` | any | (none) | Forwarded to the provider's `chat()` call verbatim. Anthropic rejects `temperature` + `top_p` together. |

To switch from Gemini to Anthropic, edit three lines:
```yaml
provider:
  name: anthropic
  model: claude-haiku-4-5
  api_key_env: ANTHROPIC_API_KEY
```

---

## `tools`

Selects which tools are enabled and configures each one.

```yaml
tools:
  enabled: [ls, bash_exec]    # explicit list; unknown names fail at startup
  config:
    ls:
      max_depth: 2
      show_hidden: false
    bash_exec:
      timeout_seconds: 30
      max_output_chars: 50000
      working_directory: null  # null = inherit runtime.workspace
```

| Tool | Config key | Type | Default | Meaning |
|---|---|---|---|---|
| `ls` | `max_depth` | int | `2` | Maximum recursion depth. |
| `ls` | `show_hidden` | bool | `false` | Include dotfiles. |
| `bash_exec` | `timeout_seconds` | int | `30` | Per-command timeout. |
| `bash_exec` | `max_output_chars` | int | `50000` | Truncate combined stdout+stderr at this size. |
| `bash_exec` | `working_directory` | string\|null | `null` | Override cwd. `null` = use `runtime.workspace`. |

To enable a custom tool, add its name to `enabled:` and a config block under
`config:`. See [`tool-authoring.md`](tool-authoring.md).

---

## `plugins`

Selects which plugins are enabled, configures each, controls hook order.

### Top-level

| Key | Type | Default | Meaning |
|---|---|---|---|
| `failure_threshold` | int | `3` | After N exceptions, a plugin is quarantined. |
| `exception_message_max_chars` | int | `500` | Truncate exception messages in events at N chars. |
| `enabled` | list | (5 plugins) | Ordered list of `PluginEntry` objects. |

### `enabled[i]`

Each entry:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | — | Plugin name. Must exist in `_BUILDERS`. |
| `enabled` | bool | `true` | Set `false` to load-but-skip without removing config. |
| `hooks_order` | dict | `{}` | `<hook_name>: <int priority>`. Lower = earlier. |
| `config` | dict | `{}` | Plugin-specific. See per-plugin tables below. |

### `jsonl-recorder`

Takes no config. Persists every event to `<session_dir>/events.jsonl`.
Recommended hooks_order: `on_session_start: 10`, `on_event: 100`,
`on_session_end: 10`.

### `guard`

Tool-call policy layer.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `allowlist_tools` | list[str] | `["ls"]` | Tools that bypass all guard checks. |
| `blocklist_patterns` | list[regex] | (see defaults) | Regex against tool input `command` field. Match → deny. |
| `escalation_required_patterns` | list[regex] | (see defaults) | Match → prompt user via `UserGate`. Headless = auto-deny. |

### `pause-resume`

Takes no config. Watches `<session_dir>/pause` signal file and an in-process
flag (set by Ctrl+C in TUI). Raises `PauseRequested` at next checkpoint.

### `log-writer`

Human-readable session.log writer.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `level` | string | `"info"` | Filter threshold. |
| `preview_chars` | int | `200` | Truncate long messages/outputs in the log. |
| `include_events` | list[str] | `[]` | If non-empty, ONLY log these event types. |
| `exclude_events` | list[str] | `[]` | Event types to skip. |

### `sliding-window-context`

Drops oldest user-turn fragments when over budget.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `keep_first_turns` | int | `2` | Always preserve the N oldest user-turn fragments. |
| `keep_last_turns` | int | `20` | Always preserve the N most-recent fragments. |
| `max_tokens` | int\|null | `null` | Token budget. `null` = no budget, only turn count. |
| `token_estimate_chars_per` | int | `4` | Heuristic: tokens ≈ chars / N. |

---

## `tui`

Controls the interactive TUI. Headless modes (`arc run`, `arc replay`)
ignore this section.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | bool | `true` | Master switch. `false` = headless. |
| `theme` | string | `"default"` | Color theme. Currently only `default`. |
| `inline_mode` | bool | `true` | `true` = scrollback works. `false` = alt-screen (don't). |
| `spinner_style` | string | `"dots"` | Rich spinner style. |
| `prompt_prefix` | string | `"❯ "` | Prefix in front of user input. |
| `show_token_counts` | bool | `true` | Show per-turn token counts in the footer line. |
| `show_event_count` | bool | `false` | Debug aid; appends event count to footer. |
| `show_thinking` | bool | `true` | Render `thinking` blocks (Claude 3.7+/4+) in TUI. Always preserved in logs regardless. |
| `tool_output_max_lines` | int | `30` | Collapse tool outputs longer than N lines into a one-line summary. |
| `toolbar_enabled` | bool | `true` | Persistent bottom toolbar with provider/model/session/turn/tokens/$cost. |
| `input_history_enabled` | bool | `true` | Up/down recalls past inputs from `$ARC_HOME/history`. |
| `subagent_activity` | bool | `true` | Stream a sub-agent's tool calls into the scrollback as nested `↳` lines. |
| `tabs_max` | int | `4` | Max open session tabs (0026 time travel). Branches open in a new tab; each live tab holds its own plugins + MCP connections, so keep this small. |

---

## `bootstrap`

One-time bootstrap behavior (used by `arc bootstrap`).

| Key | Type | Default | Meaning |
|---|---|---|---|
| `create_workspace_dir` | bool | `false` | Create `runtime.workspace` if it doesn't exist. |
| `write_example_session` | bool | `false` | Seed an example session for replay testing. |

---

## `ARC_HOME` resolution

The config file lives at `$ARC_HOME/config.yml`. `$ARC_HOME` resolves in
this order:

1. `--home <path>` CLI flag (per-invocation override)
2. `ARC_HOME` env var
3. `./.arc/` (in cwd, if it exists — useful for per-project configs)
4. `~/.arc/` (default fallback)

Inspect what arc resolved:

```bash
arc config path     # prints the path
arc config show     # prints the loaded config
```

---

## Backwards compatibility

When new config keys are added, the loader treats them as optional with
sensible defaults. Existing `config.yml` files keep loading without a
re-bootstrap. The four `tui.*` keys added in `_design/0011-tui-polish.md`
are an example of this — they default to `true` / `30` if missing.

To opt in to the latest defaults wholesale:

```bash
arc bootstrap --force      # overwrites your config.yml with the current defaults
```

Back up first if you've customized.
