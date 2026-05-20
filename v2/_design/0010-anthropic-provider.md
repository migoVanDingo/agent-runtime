# 0010 — Anthropic provider

**Status:** complete
**Phase:** 3.1 (first multi-provider work)
**Implements:** the `LLMProvider` Protocol for Anthropic Claude models

## 1. Goals

1. Add Anthropic as a second provider alongside Gemini. Users switch via
   `provider.name: anthropic` in `config.yml`.
2. Same universal types (`Message`, `ContentBlock`, `LLMRequest`,
   `LLMResponse`) — no new shapes leak into the runtime.
3. Byte-faithful response capture for replay, same as Gemini.
4. Live integration test against real Anthropic API.

## 2. Non-goals

- **No multi-provider abstraction layer.** Each provider is its own class
  implementing the Protocol. The factory in `providers/__init__.py` knows
  about both. Adding a third (Grok, OpenAI) is one more case + one more
  file.
- **No model routing.** One provider per session, set via config. Cross-
  provider routing (e.g., haiku for planning, sonnet for execution) is a
  future plugin if needed.
- **No streaming.** Sync `messages.create()` only, matching Gemini.

## 3. Design decisions

### A. Byte-fidelity verified — `resp.model_dump(mode="json")` round-trips

Probe in `_tests/experiment_anthropic_sdk_fidelity.py` confirmed: 559 bytes
of canonical JSON for a "pong" response, round-trips through
`json.dumps + json.loads` cleanly. Store this dict in
`LLMResponse.raw` for replay, same as Gemini.

### B. Tool-use ID matching uses position-from-prev-assistant

Anthropic REQUIRES each `tool_result` block to reference a `tool_use_id`
that matches a `tool_use` block in the previous assistant message.
Anthropic enforces this at the API layer — mismatched IDs return 400.

Problem: the universal `Message` type doesn't carry `tool_call_id` on
the tool-role message. The loop's `_messages.append(...)` for tool
results uses a Gemini-shaped `function_response` dict that has the
name but not the original tool_use_id.

**Solution: position-based matching in the Anthropic provider.** When
translating messages:
- Walking the message list, the most recent assistant message's `tool_use`
  blocks are tracked in order with their IDs.
- Tool-role messages that follow are matched to those IDs by position
  (1st tool message → 1st tool_use_id, etc.).
- This works for sequential AND parallel tool calls in one iteration.

The alternative — modifying the loop to include tool_call_id in the
appended tool message — would be cleaner but requires changing the
universal Message type and migrating recorded events.jsonl. Defer that
for now; position-matching is correct and adequate.

### C. System prompt is a top-level parameter, not a message

Anthropic accepts `system` as a separate parameter on `messages.create()`,
not as a message in the list. The provider passes `req.system` directly
to that parameter. Gemini does the same via `GenerateContentConfig`.
No change to the universal `LLMRequest` needed.

### D. Stop-reason translation

| Anthropic | Universal |
|-----------|-----------|
| `end_turn` | `end_turn` |
| `tool_use` | `tool_use` |
| `max_tokens` | `max_tokens` |
| `stop_sequence` | `other` |
| `pause_turn` | `other` |
| anything else | `other` |

### E. Tool-result content goes back as user messages

Anthropic doesn't have a separate "tool" role. Tool results are user
messages with `tool_result` content blocks. Each tool message in our
universal format becomes its own `{"role": "user", "content": [tool_result]}`.

This is fine — the conversation looks slightly different on the wire
than Gemini's but the semantic flow is identical.

### F. The `anthropic` package goes in core dependencies, not optional

We could mark it optional and import lazily, but the simpler choice is
to install it. It's a small package, used by every Anthropic user.
Adding `extras_require` for "anthropic-only" installs is more complexity
than it saves.

## 4. New files

```
src/arc/providers/
  anthropic.py              AnthropicProvider implementation
```

Updates:
- `pyproject.toml` — add `anthropic>=0.30` to dependencies
- `arc/providers/__init__.py` — register builder for "anthropic"
- `arc/defaults.py` — add a comment showing how to switch (no default change)

Tests:
- `tests/unit/test_anthropic_provider.py` — mocked SDK
- `tests/integration/test_anthropic_live.py` — real API (skipped without key)

## 5. Switching providers

User edits `config.yml`:

```yaml
provider:
  name: anthropic
  model: claude-haiku-4-5
  api_key_env: ANTHROPIC_API_KEY
  ...
```

That's the whole knob. Everything else just works.

## 6. Implementation notes

### 6.1 What landed

| Task | File(s) | Status |
|------|---------|--------|
| #96 Design doc + byte-fidelity probe | this file + `_tests/experiment_anthropic_sdk_fidelity.py` | ✅ |
| #97 Provider implementation | `arc/providers/anthropic.py` | ✅ |
| #98 Wire (factory + deps + defaults note) | `arc/providers/__init__.py`, `pyproject.toml`, `arc/defaults.py` | ✅ |
| #99 Tests (17 unit + 3 live) | `tests/unit/test_anthropic_provider.py`, `tests/integration/test_anthropic_live.py` | ✅ |

**Test coverage:** 17 mocked unit tests + 3 live integration tests
against real Anthropic. **344 tests total, all green.**

### 6.2 Live verification

`arc run` against Anthropic in the smoke test:

```
2026-05-20 19:26:43 [INFO] arc.runtime:   Session started
2026-05-20 19:26:43 [INFO] arc.runtime:   provider:   anthropic / claude-haiku-4-5
2026-05-20 19:26:43 [INFO] arc.runtime:   tools:      ls, bash_exec
2026-05-20 19:26:43 [INFO] arc.runtime:   user: List files in /tmp/arc-anthropic-smoke
2026-05-20 19:26:43 [INFO] arc.llm:   → llm.call  (claude-haiku-4-5, 1 msgs, 2 tools)
2026-05-20 19:26:44 [INFO] arc.llm:   ← llm.call  (stop=tool_use, tokens=947/60)
2026-05-20 19:26:44 [INFO] arc.tool:   → ls(path='/tmp/arc-anthropic-smoke')
2026-05-20 19:26:44 [INFO] arc.tool:   ← ls (2 lines, 20 chars)
2026-05-20 19:26:44 [INFO] arc.llm:   → llm.call  (claude-haiku-4-5, 3 msgs, 2 tools)
2026-05-20 19:26:44 [INFO] arc.llm:   ← llm.call  (stop=end_turn, tokens=1025/26)
2026-05-20 19:26:44 [INFO] arc.runtime:   assistant: The directory contains:
- `config.yml` (file)
- `sessions/` (directory)
2026-05-20 19:26:44 [INFO] arc.runtime:   turn complete  (2 llm, 1 tool)
```

Full ReAct loop end-to-end with no code changes outside `arc/providers/`
— exactly the win that the provider Protocol was designed to deliver.

### 6.3 Bug caught during live smoke: top_p + temperature collision

First Anthropic smoke run failed with:

```
Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error',
'message': '`temperature` and `top_p` cannot both be specified for this model.'}}
```

Anthropic enforces "set one or the other"; Gemini accepts both. Our
default `params` had `top_p: 1.0` from the Gemini-only era. Since
`top_p: 1.0` is a no-op (= "no nucleus sampling"), we just removed it
from defaults. Comment in `defaults.py` warns the next person not to
add it back without thinking.

The lesson: **provider quirks surface at the param level too, not just
at the response structure.** Any param the SDK accepts vs. another SDK
rejects → asymmetry. For now we keep defaults to the minimal set both
providers accept (`temperature`, `max_tokens`). If a provider-specific
knob is wanted, the user adds it to their own config.

### 6.4 Tool_use_id matching by position — works in practice

The position-based matching design held up in the live test —
claude-haiku-4-5 called `ls` once, the loop appended a tool message,
the next chat() call sent it back as `tool_result` with the matching
`tool_use_id`, and Anthropic accepted it cleanly. Parallel-tool-call
case was covered by unit tests; live test confirmed sequential works.

### 6.5 Operational state

| Thing | State |
|-------|-------|
| `provider.name: anthropic` works end-to-end | ✅ |
| Tool calls work (single + parallel) | ✅ |
| `model_dump(mode="json")` round-trips for replay | ✅ |
| Retry policy honors config | ✅ |
| Provider factory unknown-name error message lists both | ✅ |
| Default config has `top_p` removed (incompatible with Anthropic) | ✅ |
| README mentions multi-provider | not yet — minor follow-up |

## 7. Lessons

1. **The Protocol earned its keep.** Adding a second provider was almost
   pure translation code — zero runtime changes, zero new universal
   types, zero hook changes. Tests passed first attempt for everything
   except the param-collision bug (which is a config quirk, not a
   design problem).

2. **Provider quirks live in three places** — response shape (handled
   by translation), supported params (handled by config), and required
   vs. optional fields (handled by sensible defaults). All three need
   testing against the real API; mocks can't catch e.g. the top_p
   collision.

3. **Position-based matching for tool_use_id is the right minimum
   viable design.** Modifying the universal `Message` type to carry
   tool_call_id on tool messages would have been cleaner but would
   have required migrating recorded events.jsonl and updating both
   providers. Position-matching adds zero migration cost and works
   for every realistic flow. Future-us can do the cleaner refactor
   if a use case forces it.

4. **`model_dump(mode="json")` is the universal Anthropic/Google
   serialization knob.** Both modern SDKs ship pydantic-based response
   models and `model_dump(mode="json")` round-trips on both. Future
   providers should be expected to support it; if a new provider doesn't,
   that's a flag to wrap HTTP directly instead.

## 8. What's next (per user's plan)

1. ✅ README update
2. ✅ Context manager (sliding window)
3. ✅ Multi-provider (Anthropic)
4. **Sub-agents** ← next
5. TUI polish
6. Documentation pass

