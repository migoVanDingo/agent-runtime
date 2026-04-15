# 0003 — LLM Provider: Phase 1 — Normalized Response Types

## Goal

Define the shared data types and base interface that all providers must implement.
No provider implementations yet — just the contract.

---

## Files

### New: `src/providers/__init__.py`
Empty package init.

### New: `src/providers/base.py`

**Types:**
- `TextBlock` — represents a text response from the model
- `ToolUseBlock` — represents a tool call from the model (id, name, input)
- `ProviderResponse` — wraps a list of content blocks and a normalized stop_reason

**Interface:**
- `BaseProvider` ABC with one required method:
  `chat(messages, tools, system) -> ProviderResponse`

---

## Normalized stop_reason values

All providers normalize to these two strings regardless of what their SDK returns:
- `"end_turn"` — model is done, no tool calls
- `"tool_use"` — model wants to call one or more tools

---

## Notes

- Types are plain Python dataclasses — no Pydantic, no SDK dependencies
- `ToolUseBlock.input` is `dict` — already parsed from JSON by the time it hits the agent
- This file has zero external dependencies outside stdlib
