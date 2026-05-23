# Plugin authoring guide

A plugin in arc is an object that implements one or more of the 12 hook
protocols defined in `src/arc/runtime/hooks.py`. The runtime composes hooks
from all registered plugins in deterministic order; each hook returns either
`None` (pass-through) or a transformed value.

This guide walks the contract end-to-end. For the full design rationale see
[`_design/0001-foundation-phase0-design.md`](../_design/0001-foundation-phase0-design.md) §4.

---

## 1. The hook catalog

All hook protocols live in [`src/arc/runtime/hooks.py`](../src/arc/runtime/hooks.py).
Twelve hooks, each a `Protocol` with exactly one method:

| Hook | Fires | Return value |
|------|-------|---|
| `on_session_start` | once at session boot | None (observe only) |
| `on_session_end` | once at session exit | None (observe only) |
| `on_turn_start` | beginning of each user turn | replacement `UserInput` or None |
| `on_turn_end` | end of each turn | None (observe only) |
| `before_llm_call` | before each provider call | replacement `LLMRequest` or None |
| `after_llm_call` | after each provider call | replacement `LLMResponse` or None |
| `before_tool_call` | before each tool execution | replacement `ToolCall`, `ToolDenial`, or None |
| `after_tool_call` | after each tool execution | replacement `ToolResult` or None |
| `pack_context` | building messages for next LLM call | filtered `list[Message]` or None |
| `assess_step` | after each step boundary (planner-defined only) | `StepAssessment` or None |
| `on_event` | every emitted event | None (observe only) |
| `pause_check` | cooperative yield points | None, or raise `PauseRequested` / `Cancelled` |

A plugin implements any subset of these. The runtime dispatches each hook to
every plugin that defines it, in registered order, threading the return value.

---

## 2. Plugin structure

A plugin lives at `src/arc/plugins/<name>/`:

```
src/arc/plugins/your_plugin/
  __init__.py          re-exports the class
  plugin.py            the implementation
```

The class itself just defines methods matching the hook names. No base class
to inherit from; arc uses structural typing (Protocols) — if it walks like a
`BeforeToolCall`, it is one.

```python
# src/arc/plugins/your_plugin/plugin.py
from arc.runtime.hooks import TurnContext, ToolCall, ToolDenial


class YourPlugin:
    """One-sentence summary of what this plugin does."""

    name = "your-plugin"  # convention; matches the key in defaults.py

    def __init__(self, *, your_setting: int = 5):
        self._your_setting = your_setting

    def before_tool_call(
        self, ctx: TurnContext, call: ToolCall
    ) -> ToolCall | ToolDenial | None:
        if call.name == "bash_exec" and "rm" in call.input.get("command", ""):
            return ToolDenial(
                tool_call_id=call.tool_call_id,
                name=call.name,
                reason="rm not allowed by your-plugin",
            )
        return None  # pass-through
```

---

## 3. Registration: the builder + `_BUILDERS`

Every plugin needs a builder function in `src/arc/plugins/__init__.py` and
an entry in the `_BUILDERS` dict. The builder receives the per-plugin config
dict from `config.yml` and a `PluginBuildContext` with runtime-provided
dependencies.

```python
# src/arc/plugins/__init__.py

from arc.plugins.your_plugin.plugin import YourPlugin

def _build_your_plugin(cfg: dict, build_ctx: PluginBuildContext) -> Any:
    return YourPlugin(
        your_setting=int(cfg.get("your_setting", 5)),
    )

_BUILDERS = {
    # ... existing entries ...
    "your-plugin": _build_your_plugin,
}
```

`PluginBuildContext` carries:

| Field | What it is | When to use |
|---|---|---|
| `sessions_dir` | `Path` to `$ARC_HOME/sessions/` | persisting files |
| `session_id` | current session id | naming session-scoped files |
| `config_snapshot_yaml` | full config as text | the JSONL recorder uses this |
| `user_gate` | `UserGate \| None` (prompt the human) | safety/escalation plugins |
| `bus` | `EventBus \| None` (emit events back) | plugins that want to record their actions |

---

## 4. Wiring into the default config

Add an entry under `plugins.enabled:` in `src/arc/defaults.py` so it loads
out of the box:

```yaml
plugins:
  enabled:
    # ... existing ...
    - name: your-plugin
      enabled: true            # users can flip to false to disable
      hooks_order:
        before_tool_call: 75   # lower numbers fire first
      config:
        your_setting: 5
```

`hooks_order` is a per-hook integer priority. Lower numbers fire earlier.
The convention used by the built-in plugins:

| Priority | Use for |
|---|---|
| 10 – 30 | observers (recorder, logger) |
| 40 – 60 | normal mutators |
| 70 – 90 | safety / final-word denials |
| 100 | absolute last (rarely needed) |

If you skip `hooks_order` entirely, hooks fire in registration order, which
matches `plugins.enabled` order — fine for most plugins.

---

## 5. Emitting events back into the stream

Some plugins need to record *their own* decisions in the canonical event log
(e.g., context-pack records what was filtered; safety-gate records the user's
decision). They do this by accepting the `bus` from `PluginBuildContext` and
emitting a typed event:

```python
from arc.runtime.events import EventType, RuntimeEvent

class YourPlugin:
    def __init__(self, ...):
        ...
        self._bus = None

    def bind_bus(self, bus):
        """Called by the builder right after construction."""
        self._bus = bus

    def before_tool_call(self, ctx, call):
        if not_allowed(call):
            if self._bus is not None:
                self._bus.emit(EventType.RUNTIME_TOOL_FILTERED, {
                    "tool": call.name,
                    "reason": "...",
                })
            return ToolDenial(...)
```

Add a new `EventType.*` constant in `src/arc/runtime/events.py` if your event
isn't already represented. Add a formatter to `arc/plugins/log_writer/formatter.py`
so it renders in `session.log` too.

---

## 6. Observe-only vs mutating

Hooks fall into two camps:

- **Observe-only**: `on_session_start`, `on_session_end`, `on_turn_end`,
  `on_event`. Return `None` (the runtime ignores the value). Use these
  for recording, metrics, side-channel writes.
- **Mutating**: every other hook. Return `None` to pass through unchanged,
  or return a replacement to mutate the chain.

The runtime is strict: if you implement a mutating hook and return something
that isn't the right type, you'll trip a type check downstream. When in doubt,
`return None` (or the `PASS_THROUGH` sentinel — same thing, clearer at the
call site).

---

## 7. Failure handling

Plugins are isolated. If your `on_event` raises, the runtime catches it,
increments a failure counter for your plugin, and continues. After
`DEFAULT_FAILURE_THRESHOLD` (3, by default — overridable via `runtime.plugin_failure_threshold`),
your plugin is quarantined for the rest of the session and a
`plugin.quarantined` event is emitted. The session does **not** crash.

The same applies to mutating hooks — except a quarantined mutating hook
just becomes pass-through.

This means **you don't need to wrap every line in a try/except**. Let
exceptions surface; the runtime will quarantine and continue. Only catch
when you have a sensible recovery (e.g., "skip this entry but process
the rest").

---

## 8. Testing plugins

Unit tests live under `tests/unit/test_<plugin_name>.py`. The pattern is:

```python
from arc.plugins.your_plugin.plugin import YourPlugin
from arc.runtime.hooks import TurnContext, SessionContext, ToolCall

def test_denies_rm():
    plugin = YourPlugin(your_setting=5)
    ctx = TurnContext(
        session=SessionContext(
            session_id="SES_test", workspace=".",
            provider_name="fake", provider_model="fake",
            started_at="2026-01-01T00:00:00Z",
        ),
        turn_id="TRN_test", user_input="...", iteration=0,
    )
    call = ToolCall(tool_call_id="x", name="bash_exec",
                    input={"command": "rm -rf /"})
    result = plugin.before_tool_call(ctx, call)
    assert result is not None
    assert result.reason.startswith("rm not allowed")
```

For integration tests, mount your plugin into a real `HookRegistry` and run
a fake turn end-to-end. See `tests/unit/test_loop.py` for examples.

---

## 9. Worked example: the sliding-window context manager

Walk through [`src/arc/plugins/sliding_window_context/plugin.py`](../src/arc/plugins/sliding_window_context/plugin.py) — it's the most
representative non-trivial plugin in the tree:

- Implements one hook: `pack_context`
- Takes a `bus` via `bind_bus` so it can emit
  `runtime.context_packed` to record what it dropped
- Operates at user-turn fragment granularity (preserves causation chains)
- Has a `keep_first_turns` + `keep_last_turns` + `max_tokens` API so users
  can tune behavior without changing code
- ~120 lines total; covered by `tests/unit/test_sliding_window_context.py`

That's the full pattern. Define a small class, name it well, register it,
test it, ship it.
