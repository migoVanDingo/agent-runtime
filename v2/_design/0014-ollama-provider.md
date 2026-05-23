# 0014 — Ollama provider

## Motivation

v2 ships with Gemini and Anthropic.  Both are excellent and both cost
money per token.  Ollama runs models locally with zero per-token cost,
which matters for three real workflows:

- **Reverse-engineering sessions over big binaries.**  These produce
  enormous tool output (decompiled functions, strings dumps, hexdumps).
  Token spend on a remote provider gets uncomfortable fast.
- **Privacy-sensitive work.**  Internal code or untrusted binaries
  shouldn't leave the box.
- **Offline / flaky-network sessions.**  Hotel wifi, airplane mode.

v1 had a 10-line `OllamaProvider` that inherited a shared
`OpenAICompatibleProvider`.  v2 should restore that capability *and* take
the opportunity to do it right: byte-faithful `.raw`, proper tool-use
translation, capability flags for models that don't do function calling,
and a free-tier entry in the pricing table so the toolbar doesn't show
`$NaN`.

This phase also introduces a reusable **OpenAI-compat shim**
(`arc.providers.openai_compat`) that Ollama, OpenAI (eventually), DeepSeek,
Grok, and llama.cpp (next doc) can all build on.  Three providers in v1
used this base; we'll do the same.

---

## Scope

In:
- New module `arc.providers.openai_compat` — shared translation layer for
  any provider that speaks the OpenAI Chat Completions API
- New provider `arc.providers.ollama` — thin shim setting Ollama defaults
- Capability flags so models without tool-use support surface a clear
  startup error rather than a cryptic 400
- Pricing-table entry for `ollama/*` → $0 input/$0 output (TUI toolbar
  shows tokens, hides cost)
- Optional preflight: `GET /api/tags` to warn (not fail) if the configured
  model isn't pulled
- `defaults.py` example block under `provider:` (commented out, like the
  Anthropic example)

Out (deferred):
- Streaming.  v2's loop is request-response only; streaming touches the
  whole event pipeline.  Separate phase if/when we want it.
- Auto-pull of missing models (`POST /api/pull`).  Tempting but a footgun;
  let the user run `ollama pull` themselves.
- Per-model capability auto-detection (probe by sending a tiny tool-use
  request at startup).  Manual capability config flags are good enough.
- Multimodal (image input).  Ollama supports it via `images: [...]`; the
  arc `ContentBlock` schema doesn't currently model images.

---

## Architecture

```
src/arc/providers/
  base.py              ← unchanged Protocol
  gemini.py            ← unchanged
  anthropic.py         ← unchanged
  openai_compat.py     ← NEW — shared translation, ~300 lines
  ollama.py            ← NEW — ~40 lines, sets defaults + inherits compat
  __init__.py          ← +2 cases in build()
```

### `openai_compat.py` — what it does

The class implements `LLMProvider`.  In its `chat()`:

1. **Translate `LLMRequest` → OpenAI Chat Completions payload.**
   - `system` → `{"role": "system", "content": ...}` as the first message
   - `messages` flattened to `{"role", "content", "tool_calls" | "tool_call_id"}`
     - `role="assistant"` with `tool_use` blocks → `tool_calls: [...]`
       with each call's `id` and JSON-stringified `arguments`
     - `role="tool"` → `{"role": "tool", "tool_call_id": ..., "content": ...}`
     - `role="user"` text → plain `{"role": "user", "content": "..."}`
   - `tools` → `[{"type": "function", "function": {name, description,
     parameters}}, ...]`
   - `params` → `temperature`, `max_tokens` (or `max_completion_tokens`
     for OpenAI's o-series — gated by capability flag), `top_p`, etc.

2. **Call the SDK** (`openai.OpenAI(base_url=..., api_key=...).
   chat.completions.create(**payload)`) wrapped in the runtime's retry
   policy from `req.params["retry"]`.  Use the `_retry_call` helper
   pattern from the Anthropic provider — same backoff shape.

3. **Translate response → `LLMResponse`.**
   - `choices[0].message.content` → `ContentBlock(type="text", text=...)`
   - `choices[0].message.tool_calls[*]` → one `ContentBlock(type="tool_use",
     tool_use_id=tc.id, tool_name=tc.function.name,
     tool_input=json.loads(tc.function.arguments))` per call
   - Empty `arguments` string → `{}` (Ollama often emits `""` for no-arg
     tools)
   - `finish_reason` → arc canonical: `tool_calls` → `tool_use`,
     `length` → `max_tokens`, `stop` → `end_turn`, else pass through
   - `usage.prompt_tokens` / `completion_tokens` → `input_tokens` /
     `output_tokens` (Ollama populates these; some compat servers return
     0 — that's fine)
   - **`.raw = response.model_dump(mode="json")`** — critical, byte-faithful

### Capability flags

```python
@dataclass(frozen=True)
class CompatCapabilities:
    tool_use: bool = True
    parallel_tool_calls: bool = True
    json_mode: bool = True              # response_format={"type":"json_object"}
    json_schema: bool = False           # response_format={"type":"json_schema", ...}
    max_tokens_param: str = "max_tokens"   # OpenAI o-series uses "max_completion_tokens"
```

`OpenAICompatProvider` takes a `CompatCapabilities` in its constructor.
`OllamaProvider` passes the right shape; future providers (OpenAI,
DeepSeek, llama.cpp) will too.  At startup, if `tool_use=False` and the
user has tools enabled, the provider raises a clear error: "provider
ollama with model llama3.2:3b doesn't support tool calling; pick a
tool-capable model like llama3.1:8b or hermes3:3b."

### `ollama.py` — what it adds

```python
from arc.providers.openai_compat import OpenAICompatProvider, CompatCapabilities

class OllamaProvider(OpenAICompatProvider):
    name = "ollama"

    def __init__(self, cfg: ProviderConfig):
        base_url = cfg.base_url or "http://localhost:11434/v1"
        # Ollama doesn't validate api_key but the SDK requires SOMETHING
        api_key = os.environ.get(cfg.api_key_env or "OLLAMA_API_KEY", "ollama")
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=cfg.model,
            retry=cfg.retry,
            params=cfg.params,
            capabilities=_capabilities_for(cfg.model),
        )

def _capabilities_for(model: str) -> CompatCapabilities:
    # Conservative defaults; opt models known to support more
    tool_use = any(p in model for p in (
        "llama3.1", "llama3.2", "llama3.3", "hermes3", "mistral-nemo",
        "qwen2.5", "command-r", "firefunction", "granite3",
    ))
    return CompatCapabilities(
        tool_use=tool_use,
        parallel_tool_calls=tool_use,   # same set
        json_mode=True,                  # Ollama supports it for most models
        json_schema=False,
    )
```

40 lines.  All the real work lives in the shared base.

---

## Config

```yaml
provider:
  name: ollama
  model: llama3.1:8b
  api_key_env: OLLAMA_API_KEY         # optional; defaults to literal "ollama"
  base_url: http://localhost:11434/v1 # null = this default
  timeout_seconds: 120                # local inference is slower than cloud
  retry:
    max_attempts: 3
    backoff_base_seconds: 1
    backoff_max_seconds: 8
  params:
    temperature: 0
    max_tokens: 4096
    # Provider-specific knobs pass through to the SDK / Ollama:
    # top_p, top_k, repeat_penalty, num_ctx, num_gpu, seed, etc.
```

`base_url` defaults to `http://localhost:11434/v1` (with the `/v1`).
Users running Ollama on a remote host just change the host part.

`defaults.py` gets a commented-out example block under `provider:` so the
shape is visible:

```yaml
# Example: switch to Ollama (local, free, slower)
#   name: ollama
#   model: llama3.1:8b
#   base_url: http://localhost:11434/v1
#   timeout_seconds: 120
```

---

## Preflight check

On provider construction:

```python
try:
    resp = httpx.get(f"{base_url.rstrip('/v1')}/api/tags", timeout=3)
    pulled = {m["name"] for m in resp.json().get("models", [])}
    if cfg.model not in pulled and f"{cfg.model}:latest" not in pulled:
        log.warning(
            "ollama: model %r not in local cache. "
            "Run `ollama pull %s` first. "
            "(continuing — server will lazy-pull, but first turn will be slow)",
            cfg.model, cfg.model,
        )
except (httpx.HTTPError, json.JSONDecodeError):
    # Server probably isn't running; let the first chat() call surface a
    # clearer error rather than failing startup on a probe
    pass
```

Warn, don't fail.  If the user's running Ollama elsewhere or has the
model cached under a different name, we don't want to be wrong about it.

---

## Pricing

`arc.tui.pricing.PricingTable` currently fetches from LiteLLM's table for
known model ids.  Ollama models aren't in there.  Add a hardcoded floor:

```python
# In pricing.py, after upstream fetch returns its table:
def _patch_local_models(table: dict) -> dict:
    table.setdefault("ollama/*", {"input_cost_per_token": 0.0,
                                  "output_cost_per_token": 0.0})
    table.setdefault("llama_cpp/*", {"input_cost_per_token": 0.0,
                                     "output_cost_per_token": 0.0})
    return table

def lookup(self, provider: str, model: str) -> CostRates | None:
    key = f"{provider}/{model}"
    if key in self._table:
        return self._table[key]
    wildcard = f"{provider}/*"
    if wildcard in self._table:
        return self._table[wildcard]
    return None
```

TUI toolbar then shows `$0.00` for Ollama sessions instead of disappearing
or showing `$NaN`.  Token counts continue to display.

---

## Observability

No new event types — provider events (`llm.call.started`,
`llm.call.completed`, `llm.call.failed`) cover the surface.  Existing
log_writer formatters render them.

The `.raw` field captures the full Ollama response, so events.jsonl is
sufficient for replay (mode 2 deterministic).

One small addition: `llm.call.completed.metadata` already includes
`input_tokens` and `output_tokens`; we'll also write `provider_load_ms`
(Ollama returns `load_duration` for first-call-after-model-load timing)
when present, so users can see the cold-start cost in the log.

---

## Recovery and failure modes

| Failure | Behavior |
|---|---|
| Ollama server down | SDK raises `ConnectionError`; retry policy kicks in; after exhausting attempts, `llm.call.failed` with clear message ("ollama server unreachable at http://localhost:11434 — is `ollama serve` running?") |
| Model not pulled | First call returns 404; surface as "model 'X' not found; run `ollama pull X`" |
| Model loaded but doesn't support tools | Server returns 400 with a message about tools; we map to "model 'X' rejected tool_use; check capability flag or pick a different model" |
| Context-window overflow | Server returns 400 / truncates silently depending on version.  arc's sliding-window-context plugin should keep us under any normal context.  If it still overflows, surface verbatim. |
| Slow first call (cold model load) | No special handling; retry policy is shaped for it (1-8s backoff vs Anthropic's 2-32s). |
| Malformed tool_call arguments | Ollama models sometimes emit `arguments: "not-json"`.  We try `json.loads`, on failure raise a `ProviderError` that bubbles to a tool result the model can see ("invalid tool arguments: ...").  This becomes a normal tool retry. |
| Empty `.raw` (compat-only servers) | Replay won't work for that turn; recorded events still capture the translated `LLMResponse`, but mode-2 replay can't reproduce.  Should never happen with real Ollama; defensive. |

Provider errors bubble up; the runtime's existing `llm.call.failed` path
handles them.  No new error types.

---

## File layout

```
src/arc/providers/
  openai_compat.py
  ollama.py
src/arc/tui/pricing.py             ← +_patch_local_models, wildcard lookup
tests/unit/test_openai_compat.py   ← translation tests with a stubbed SDK
tests/unit/test_ollama.py          ← capability defaults, base_url plumbing
tests/integration/test_ollama_live.py  ← skips unless OLLAMA_HOST reachable
```

Plus:
- `src/arc/providers/__init__.py` — `"ollama"` case in `build()`
- `src/arc/defaults.py` — commented-out example block

`openai` SDK is already a transitive dep through Anthropic's tooling, but
make it an explicit dep in `pyproject.toml` since we're using it directly
now.

---

## Test plan

Unit (`test_openai_compat.py`, stubbed SDK):
1. System prompt placement (first message)
2. User → assistant → tool → assistant translation round-trip
3. Tool-use translation: arc `ContentBlock(type="tool_use")` ↔ OpenAI
   `tool_calls[].function.{name,arguments}`
4. Tool-result translation: arc `role="tool"` ↔ OpenAI
   `{"role":"tool","tool_call_id":...}`
5. Stop-reason mapping (`tool_calls` → `tool_use`, `length` → `max_tokens`,
   `stop` → `end_turn`)
6. `.raw` populated with `response.model_dump(mode="json")`
7. Retry policy honored on 429 / 500 / network errors
8. Capability flag: tools enabled + `tool_use=False` → clear startup error
9. `max_tokens_param` switch for o-series-style models
10. Malformed `arguments` JSON → bubbles as `ProviderError`

Unit (`test_ollama.py`):
1. Default `base_url` is `http://localhost:11434/v1`
2. Capability detection per model name (llama3.1 → tools, llama3.2:1b →
   no tools, hermes3 → tools)
3. `api_key_env` defaults to placeholder when env var missing
4. `_capabilities_for` covers known-good model families

Integration (`test_ollama_live.py`):
1. Skip unless `OLLAMA_HOST` env var set
2. Send a one-shot "say hi" turn, assert structure and token counts
3. Send a tool-using turn against the `ls` tool, assert full round-trip
4. Replay the recorded session, assert byte-faithful

Smoke:
- `arc bootstrap`, edit config to set `provider.name: ollama` +
  `model: llama3.1:8b`, run `arc run "list the files in this directory"`,
  confirm tool is called, response is reasonable, session.log readable.

---

## State

Planned.
