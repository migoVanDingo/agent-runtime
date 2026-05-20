# 0009 — Context manager (Phase A: sliding window)

**Status:** complete
**Phase:** 3.0 (first capability plugin)
**Implements:** the `pack_context` hook from `0001-foundation-phase0-design.md` §4.4

## 1. What this is

A plugin that filters the conversation message list before each LLM call so
the model never receives more history than it needs. The most common
failure mode in long-running agent sessions — context window blowup from
accumulated tool outputs — is solved by dropping older fragments while
preserving the original goal and recent activity.

This is **Phase A** of the context manager work — the simplest possible
strategy. Smarter strategies (AFM scoring, summarization) land as their
own separate plugins later. The user enables one strategy at a time;
choice is in `config.yml`.

## 2. Goals

1. **Sliding window over fragments, not messages.** A "fragment" = one user
   turn (user message + all assistant + tool messages that follow before
   the next user turn). Drop whole fragments, never split one — splitting
   leaves orphan tool_use/tool_result pairs and confuses the model.
2. **Always keep first N + last M fragments.** First preserves the
   original goal/setup. Last preserves recent context. The model never
   loses what the user originally asked or what just happened.
3. **Optional token-budget enforcement.** If after fragment filtering the
   message list is still over a configured budget, drop additional
   fragments from the middle (preserving first and last) until under.
4. **Observable.** Emit `runtime.context_packed` event when packing
   actually changes anything, with stats (n_before, n_after, bytes_dropped).
   Shows up in `events.jsonl` and `session.log`.
5. **No surprises.** With generous defaults (keep first 2, keep last 20),
   any conversation under ~20 turns is untouched.

## 3. Non-goals

- **No scoring**, no importance ranking. Phase B adds AFM as a separate
  plugin.
- **No summarization**, no compaction. Phase C adds the summarize-context
  plugin separately.
- **No paging.** v1 paged big tool outputs to disk; that was the source of
  v1's worst bug. If the model needs an old tool output, it can re-call
  the tool. Sliding-window only drops; it never rewrites.
- **No per-message scoring or metadata.** Messages are plain `Message`
  objects; no `.importance` or `.score` fields added.

## 4. Design decisions

### A. Fragment-level, not message-level

Splitting a tool_use from its tool_result would confuse the model.
Splitting an assistant message that introduces a tool call from the call
itself would also confuse it. The simplest safe unit is the user turn:
everything from one user message up to (but not including) the next.

```python
def split_into_fragments(messages):
    fragments = []
    current = []
    for msg in messages:
        if msg.role == "user" and current:
            fragments.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        fragments.append(current)
    return fragments
```

A fragment has exactly one user message (its first). Whole fragments are
the unit we keep or drop.

### B. Keep-first + keep-last, drop the middle

Standard sliding window. The "first N" preserves the original goal — for
RE workflows that's "analyze /path/to/binary" or similar. The "last M"
preserves the recent reasoning the model needs to continue. The middle
is the bulk that's safe to drop (older tool outputs, intermediate
investigations).

### C. Token budget is optional + applied AFTER fragment filtering

If `max_tokens` is set, we estimate tokens for the kept fragments. If
still over budget, drop additional fragments from the LATEST middle (so
oldest of the keep_last set goes first, preserving the most recent and
the original first turns). Re-check, repeat. Hard floor: never drop
below `keep_first_turns + 1` fragments — leaves at least the goal and
one recent fragment.

If we can't get under budget even at the hard floor, emit a
`runtime.context_overflow` event at WARN level and pass through what's
left. Don't crash. Let the provider tell us if it's actually over the
real window.

### D. Token estimation is local + cheap

`chars / 4` (configurable via `token_estimate_chars_per`). This is a
crude approximation but fast and dep-free. Off by ~10-20% from real
tokenizer counts, which is fine for budgeting: pick a budget 80% of the
real window and you'll never overflow.

If accuracy matters, a future plugin can use `provider.count_tokens()`
via a hybrid scheme. Not in scope for Phase A.

### E. Emit an event when packing actually happens

```
runtime.context_packed  payload={
  n_messages_before: 47,
  n_messages_after: 21,
  n_fragments_before: 8,
  n_fragments_after: 3,
  bytes_dropped: 12450,
  budget_max_tokens: 100000,  # if set
  estimated_tokens_after: 5_240,
}
```

Skip the event if `n_before == n_after` (no actual filtering happened).
Keeps the log clean for short sessions where the plugin is a no-op.

### F. Default to ON with generous values

```yaml
- name: sliding-window-context
  config:
    keep_first_turns: 2
    keep_last_turns: 20
    max_tokens: null               # null = no token budget, just turn count
    token_estimate_chars_per: 4
  hooks_order:
    pack_context: 100
```

For a 5-turn conversation: no-op. For a 100-turn conversation: keeps
first 2 + last 20 = 22 fragments, drops 78. Safe default that catches
the problem without surprising anyone.

## 5. Plugin shape

```python
class SlidingWindowContextPlugin:
    name = "sliding-window-context"
    version = "1.0.0"

    def __init__(self, *, keep_first_turns, keep_last_turns,
                 max_tokens, token_estimate_chars_per):
        ...

    def pack_context(self, ctx, messages, query) -> list[Message] | None:
        # 1. Split messages into fragments (user turns)
        # 2. If total fragments <= keep_first + keep_last: pass-through (return None)
        # 3. Build kept list: first N + last M
        # 4. If max_tokens set: enforce budget by dropping more from middle
        # 5. Emit runtime.context_packed event
        # 6. Return flat message list
```

`pack_context` returns `None` for "no change" (PASS_THROUGH) when the
conversation is short enough that no filtering happened. That's the
hot path; we want it fast.

## 6. Why this design enables A/B testing later

Future strategies are separate plugins:

```
arc/plugins/
  sliding_window_context/      Phase A (this doc)
  afm_context/                 Phase B — importance scoring
  summarize_context/           Phase C — compaction via summarization
```

Each implements `pack_context`. To A/B test, the user enables one in
config and disables the others. Same hook, same input, same output
shape — comparable across plugins. The runtime doesn't know which
strategy is active.

Multiple strategies could even compose via `hooks_order`: sliding_window
runs first (drops oldest), then summarize runs (compacts what remains).
Phase A doesn't rely on this but the architecture allows it.

## 7. Acceptance test

**Setup:** synthetic test that builds a 30-message conversation by hand
(10 user turns, each with assistant + tool messages), runs `pack_context`
with `keep_first_turns=1`, `keep_last_turns=2`. Asserts:

- Result has exactly 3 fragments (1 + 2)
- First fragment is the original first user turn
- Last 2 fragments are the most recent
- `runtime.context_packed` event fired with correct stats
- Total message count: matches the kept fragments' flat sum
- No tool_use/tool_result pairs were split

Plus a real-Gemini integration test: run a session that produces enough
tool calls to exceed the default `keep_last_turns` window, verify the
LLM still gets coherent context.

## 8. Open questions (deferred)

1. **Should we count the system prompt against the budget?** No — it's
   sent separately via `LLMRequest.system`, not as a message. Out of
   scope for `pack_context`.
2. **What if a single fragment exceeds the budget?** Currently we'd keep
   it (it's a kept first or last) and overflow. Future: option to
   compact-in-place (head/tail truncation). Defer to Phase C.
3. **Multi-turn deferred summary?** When summarize-context lands, an
   open question is whether to summarize each drop event incrementally
   or build a single rolling summary across the session.

## 9. Implementation notes

### 9.1 What landed

| Task | File(s) | Status |
|------|---------|--------|
| #92 Design doc | this file | ✅ |
| #93 Plugin | `arc/plugins/sliding_window_context/plugin.py` | ✅ |
| #94 Wire (event + formatter + factory + defaults) | `runtime/events.py`, `log_writer/formatter.py`, `plugins/__init__.py`, `defaults.py` | ✅ |
| #95 Tests | `tests/unit/test_sliding_window_context.py`, `tests/integration/test_context_manager_acceptance.py` | ✅ |

**Test coverage:** 18 unit tests (fragment splitter + plugin logic + budget +
event emission) + 4 acceptance tests against real Gemini. **324 tests total, all green.**

### 9.2 Live sample

session.log line when packing fires:
```
2026-05-20 18:00:01.234 [INFO] arc.runtime:   context packed: 24 → 6 messages, 1820 bytes dropped
```

### 9.3 Bus injection pattern

For the plugin to emit `runtime.context_packed` events, it needs access to
the `EventBus`. Added a `bus: Any = None` field to `PluginBuildContext`,
and `_build_sliding_window_context` calls `plugin.bind_bus(build_ctx.bus)`
after construction. Pattern usable by any future plugin that emits.

Cleanest possible design — bus optional, plugins that don't need it ignore
it, plugins that do need it bind cleanly.

### 9.4 Resume chain limitation discovered

The acceptance tests originally tried to build a long conversation via
`arc resume` chains. They failed because **`arc resume` only restores
messages from the IMMEDIATE prior session, not the entire `resumed_from`
chain.** So chaining `run → resume → resume → resume` produces sessions
with only 1-2 turns each, never enough to trigger packing.

This isn't a context-manager bug; it's a known limitation of the resume
implementation (`arc/resume/reconstruct.py:messages_from_session` only
reads the one source session's events). Documented here so the next
person working on resume knows to consider chain aggregation.

**Acceptance test fix:** build the long history by constructing an
`AgentSession` directly with `initial_messages=<long_list>` and running
one turn. This exercises the plugin without depending on resume.

### 9.5 Defaults are generous on purpose

`keep_first_turns: 2, keep_last_turns: 20` means any session under 22
user turns is a no-op. That covers >95% of normal sessions. The plugin
quietly does nothing until conversations are genuinely long. No surprises.

For RE workflows specifically (the user's primary use case), 20 last
turns + 2 first turns is enough that recent Ghidra/r2/lldb activity stays
in context AND the original "analyze this binary" goal is preserved.

### 9.6 Operational state

| Thing | State |
|-------|-------|
| Short sessions pass through (no-op) | ✅ |
| Long sessions get filtered to (first N + last M) | ✅ |
| Optional token budget enforces below turn-count window | ✅ |
| Tool_use + tool_result pairs stay together (fragment-level drops) | ✅ |
| `runtime.context_packed` event fired with stats | ✅ |
| session.log shows packing | ✅ |
| Plugin failure isolated from runtime | ✅ |

### 9.7 What's NOT in this plugin (deferred to other plugins)

Per the design split — each strategy is its own plugin so A/B testing is
just a config swap.

- **No scoring/importance** — Phase B `afm-context` plugin
- **No summarization** — Phase C `summarize-context` plugin
- **No compaction within a kept fragment** — head/tail truncation
  would live in `summarize-context` or a dedicated `compact-context`
- **No tool-output paging** — explicitly off the table (v1's worst bug)

## 10. Lessons

1. **Bus access for emitting plugins should be threaded via
   `PluginBuildContext`.** Other plugins that need to emit (future AFM,
   summarize, maybe metrics plugins) can use the same pattern. Don't
   reach for globals.
2. **Plugin = strategy, not framework.** Each context strategy is a
   separate plugin. Users enable one. Future plugin authors compose
   their own from the primitives in `messages_from_events` + the
   `pack_context` hook.
3. **Fragments, not messages, are the safe unit.** Splitting a
   tool_use/tool_result pair breaks the model. The user-turn fragment
   is the natural cohesive unit and the safe drop granularity.
4. **Discovered limitation worth flagging:** `arc resume` doesn't
   aggregate the resume chain. Should be fixed before sub-agents land
   (they'll likely chain heavily).

## 11. What's next

Per user's stated plan:
1. ✅ This (sliding-window context manager)
2. Multi-provider — add Anthropic
3. Sub-agents
4. TUI polish
5. Documentation pass

Future context-manager plugins (Phase B/C) land when long sessions
actually demand more sophistication. Until then, this is enough.

