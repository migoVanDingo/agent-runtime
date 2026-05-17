# 0007 — LLM Provider: Phase 5 — Wire into Agent

## Goal

Replace the direct Anthropic SDK usage in `agent.py` with the `BaseProvider` interface.
The agent loop logic does not change — only how the client is instantiated and how
responses are consumed.

---

## Files

### Updated: `src/agent.py`

**Remove:**
- `import anthropic`
- `self.client = anthropic.Anthropic(api_key=...)`
- `self.model = settings.anthropic_model`
- `self.client.messages.create(...)` call
- `hasattr(block, "text")` check — no longer needed, we have typed blocks

**Add:**
- `from providers.factory import get_provider`
- `from providers.base import ProviderResponse, TextBlock, ToolUseBlock`
- `self.provider = get_provider()`
- `self.provider.chat(messages, tools, system)` replacing the SDK call

**Response handling changes:**
- `response.stop_reason` — same field name, no change
- `response.content` — now our normalized types, not SDK objects
- Text extraction: check `isinstance(block, TextBlock)` instead of `hasattr(block, "text")`
- Tool dispatch: check `isinstance(block, ToolUseBlock)` instead of `block.type == "tool_use"`

---

## Notes

- `settings` import in `agent.py` can be removed — provider owns its own config now
- `messenger.py` still receives `response.content` but now as our normalized types,
  not SDK objects — the `.model_dump()` call in `add_assistant_message` will break.
  That is addressed in Phase 6.
