# 0005 — LLM Provider: Phase 3 — Ollama Provider

## Goal

Implement `OllamaProvider` using the `openai` SDK pointed at Ollama's local endpoint.
Translate between our internal Anthropic-style message format and OpenAI format in both
directions — inbound (response → normalized types) and outbound (messages → OpenAI format).

---

## Files

### New: `src/providers/ollama.py`

Implements `BaseProvider`. Owns all Ollama/OpenAI format translation.

**`__init__(base_url, model)`**
- Creates `openai.OpenAI(base_url=base_url, api_key="ollama")` internally

**`chat(messages, tools, system) -> ProviderResponse`**
- Translates outbound messages from internal format → OpenAI format
- Prepends system prompt as a `{"role": "system", ...}` message
- Calls `client.chat.completions.create()`
- Translates inbound response → normalized `ProviderResponse`

---

## Outbound message translation (internal → OpenAI)

Our internal format uses Anthropic-style messages. The Ollama provider must translate:

| Internal format | OpenAI format |
|---|---|
| `{"role": "user", "content": "string"}` | unchanged |
| `{"role": "assistant", "content": [{"type": "text", "text": "..."}]}` | `{"role": "assistant", "content": "..."}` |
| `{"role": "assistant", "content": [{"type": "tool_use", ...}]}` | `{"role": "assistant", "tool_calls": [...]}` |
| `{"role": "user", "content": [{"type": "tool_result", ...}]}` | `{"role": "tool", "tool_call_id": ..., "content": ...}` |

---

## Inbound response translation (OpenAI → normalized)

| OpenAI response | Normalized type |
|---|---|
| `message.content` (string) | `TextBlock` |
| `message.tool_calls[n]` | `ToolUseBlock` |
| `finish_reason: "stop"` | `stop_reason: "end_turn"` |
| `finish_reason: "tool_calls"` | `stop_reason: "tool_use"` |

---

## Notes

- `api_key="ollama"` is a placeholder — Ollama doesn't require auth but the SDK requires a value
- Tool call arguments arrive as a JSON string from Ollama — must be parsed to dict for `ToolUseBlock.input`
- System prompt is injected as the first message in the OpenAI format (no dedicated system param)
- Agent and messenger are not wired up yet — that is Phase 5
