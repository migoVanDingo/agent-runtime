# 0004 — LLM Provider: Phase 2 — Anthropic Provider

## Goal

Wrap the existing Anthropic SDK usage into a `BaseProvider` implementation.
Translate Anthropic SDK responses into the normalized types defined in Phase 1.

---

## Files

### New: `src/providers/anthropic.py`

Implements `BaseProvider`. Owns the Anthropic client and all SDK-specific translation.

**`__init__(api_key, model)`**
- Creates `anthropic.Anthropic(api_key=api_key)` internally

**`chat(messages, tools, system) -> ProviderResponse`**
- Calls `client.messages.create()`
- Translates response content blocks:
  - Anthropic `TextBlock` → our `TextBlock`
  - Anthropic `ToolUseBlock` → our `ToolUseBlock`
- Returns `ProviderResponse(stop_reason, content)`

---

## Translation map

| Anthropic SDK         | Normalized type   |
|-----------------------|-------------------|
| block.type == "text"  | TextBlock         |
| block.type == "tool_use" | ToolUseBlock   |
| stop_reason           | passes through    |

---

## Notes

- `stop_reason` needs no translation — Anthropic already uses `"end_turn"` and `"tool_use"`
- Boundary conversion (SDK objects → plain types) happens here, not in messenger
- Agent and messenger are not wired up yet — that is Phase 5
