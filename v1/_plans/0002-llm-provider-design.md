# 0002 — LLM Provider Abstraction: Design

## Overview

Introduce a provider abstraction layer so the agent can run against either the Anthropic API
or a locally running Ollama instance, configurable via environment variables.
The agent loop must not change — only the provider underneath is swapped.

---

## Phases

### Phase 1 — Normalized Response Types
**File:** `src/providers/base.py`

Define the shared data types that all providers must produce:
- `TextBlock` — normalized text response
- `ToolUseBlock` — normalized tool call (name, id, input)
- `ProviderResponse` — wraps content list + stop_reason

Also defines `BaseProvider` ABC with a single required method:
```python
def chat(self, messages: list[dict], tools: list[dict], system: str) -> ProviderResponse
```

No provider implementations yet — just the contract and types.

---

### Phase 2 — Anthropic Provider
**File:** `src/providers/anthropic.py`

Wraps the existing Anthropic SDK usage from `agent.py` into the `BaseProvider` interface.
- Translates `ProviderResponse` ← Anthropic SDK response
- Moves boundary conversion (SDK objects → normalized types) here, out of `messenger.py`
- `AnthropicProvider.__init__` takes api_key and model

---

### Phase 3 — Ollama Provider
**File:** `src/providers/ollama.py`

Implements `BaseProvider` using the `openai` SDK pointed at Ollama's local endpoint.
- Translates `ProviderResponse` ← OpenAI-format response
- Normalizes `finish_reason: "tool_calls"` → `stop_reason: "tool_use"`
- Normalizes `finish_reason: "stop"` → `stop_reason: "end_turn"`
- Handles OpenAI tool call format → `ToolUseBlock`
- `OllamaProvider.__init__` takes base_url and model

---

### Phase 4 — Settings + Provider Factory
**File:** `src/settings.py` (update), `src/providers/factory.py` (new)

Add provider config fields to settings:
```
LLM_PROVIDER=anthropic        # or "ollama"
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

`factory.py` reads settings and returns the correct `BaseProvider` instance.
Single function: `get_provider() -> BaseProvider`

---

### Phase 5 — Wire into Agent
**File:** `src/agent.py` (update)

- Remove direct `anthropic` import and client instantiation
- Inject `BaseProvider` via `get_provider()`
- Replace `self.client.messages.create(...)` with `self.provider.chat(...)`
- Agent loop now operates entirely on normalized `ProviderResponse` types

---

### Phase 6 — Messenger Cleanup
**File:** `src/messenger.py` (update)

Currently `add_assistant_message` calls `.model_dump()` on Anthropic SDK objects.
With Phase 2 moving boundary conversion into the provider, the messenger receives
already-normalized dicts. Remove the SDK-specific `.model_dump()` call.

---

## Dependency Flow (post-implementation)

```
agent.py
  └── BaseProvider  (interface)
        ├── AnthropicProvider  → anthropic SDK
        └── OllamaProvider     → openai SDK (pointed at localhost)

providers/factory.py  ← reads settings, returns correct provider
providers/base.py     ← TextBlock, ToolUseBlock, ProviderResponse, BaseProvider
settings.py           ← LLM_PROVIDER, OLLAMA_BASE_URL, OLLAMA_MODEL
```

---

## What does not change

- `messenger.py` interface (add_user_message, add_tool_results, get_messages)
- `tools/` — registry, base, implementations
- `main.py`
- The agent loop logic in `agent.py`

---

## Open Questions / Future Extensions

- Add an `OpenAIProvider` — Ollama adapter already uses the openai SDK, so this is minimal work
- Provider-level retry/timeout config
- Model capability flags (e.g. `supports_tool_calling: bool`) so the agent can degrade gracefully
  with models that don't support tools
