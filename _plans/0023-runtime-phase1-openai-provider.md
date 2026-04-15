# 0023 — Runtime Infrastructure Phase 1: OpenAI Provider + Multi-Provider Factory

## What

Add OpenAI as a third LLM provider and refactor the factory to support
per-component provider selection. This unblocks using gpt-4o-mini (or any
OpenAI model) for lightweight runtime calls while keeping the main agent
on Anthropic or Ollama.

## Changes

### New files

- **`src/providers/openai_compat.py`** — `OpenAICompatibleProvider` base
  class. Extracts all the OpenAI SDK translation logic (messages, tools,
  response mapping) that was previously duplicated / lived only in
  `OllamaProvider`. Both `OllamaProvider` and `OpenAIProvider` inherit
  from this. Also adds `max_tokens` passthrough from config (the old
  Ollama provider didn't send max_tokens).

- **`src/providers/openai_provider.py`** — `OpenAIProvider`. Thin
  subclass that sets `self.client = openai.OpenAI(api_key=...)`. 7 lines.

### Modified files

- **`src/providers/ollama.py`** — Slimmed to a thin subclass of
  `OpenAICompatibleProvider`. Sets `self.client` with the Ollama base_url
  and dummy api_key. All translation logic removed (now inherited).

- **`src/providers/factory.py`** — `get_provider()` gains optional
  `provider_name` and `model_override` params for per-component provider
  selection. New `get_runtime_provider()` convenience function that reads
  `RUNTIME_PROVIDER` / `RUNTIME_MODEL` from settings, falling back to the
  main provider.

- **`src/settings.py`** — New fields:
  - `openai_api_key` (OPENAI_API_KEY)
  - `openai_model` (OPENAI_MODEL, default "gpt-4o-mini")
  - `runtime_provider` (RUNTIME_PROVIDER, default None = use main)
  - `runtime_model` (RUNTIME_MODEL, default None = use provider default)

### .env additions (user-managed)

```ini
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

RUNTIME_PROVIDER=openai
RUNTIME_MODEL=gpt-4o-mini
```

## What does not change

- `AnthropicProvider` — unchanged
- `BaseProvider` interface — unchanged
- `agent.py` — unchanged (still calls `get_provider()` with no args)
- All existing behavior — `get_provider()` with no args returns the same
  provider as before
