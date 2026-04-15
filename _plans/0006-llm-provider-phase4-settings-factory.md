# 0006 — LLM Provider: Phase 4 — Settings + Provider Factory

## Goal

Add provider configuration to settings and create a factory that reads those settings
and returns the correct `BaseProvider` instance. Agent gets a provider without knowing
which one it is.

---

## Files

### Updated: `src/settings.py`

Add three new fields:
- `llm_provider` — `"anthropic"` or `"ollama"`, defaults to `"anthropic"`
- `ollama_base_url` — defaults to `"http://localhost:11434/v1"`
- `ollama_model` — defaults to `"llama3.2"`

### New: `src/providers/factory.py`

Single function: `get_provider() -> BaseProvider`

Reads `settings.llm_provider` and returns the appropriate provider instance:
- `"anthropic"` → `AnthropicProvider(api_key, model)`
- `"ollama"` → `OllamaProvider(base_url, model)`
- anything else → raises `ValueError`

---

## Environment variables

```
LLM_PROVIDER=anthropic        # or "ollama"
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3.2
```

---

## Notes

- `factory.py` is the only place that imports both providers — keeps cross-provider imports out of agent
- Agent is not updated yet — that is Phase 5
