# 0015 — llama.cpp provider

## Motivation

Ollama (0014) covers the common local-inference path, but llama.cpp's
`llama-server` is the right fit for users who want:

- **Grammar-constrained generation (GBNF).**  Small models (3B–8B) emit
  invalid JSON or malformed tool calls often enough to break sessions.
  llama.cpp's GBNF grammar forces the model to *only* produce tokens
  that match a given grammar, eliminating the failure mode entirely.
- **Single-model, low-overhead serving.**  No model-manifest layer; just
  a binary, a .gguf file, and a port.  Lower memory floor than Ollama
  at the same model size.
- **Speculative decoding, draft models, mlock, mmap controls** — knobs
  Ollama abstracts away that some users actively want.
- **Edge devices / weird hardware.**  llama.cpp runs on Metal, CUDA,
  ROCm, Vulkan, CPU-only, and a half-dozen other backends.  Ollama
  inherits from llama.cpp but lags it on platform support.

`llama-server` exposes two HTTP surfaces:

1. **`/v1/chat/completions`** — OpenAI-compatible.  Reuses 0014's
   `OpenAICompatProvider` shim with no new translation code.
2. **`/completion`** — native, supports `grammar` (GBNF) and richer
   sampling controls.  Needed for grammar-constrained tool use.

This phase ships both modes under one provider, defaulting to compat
mode for ergonomics and falling back to native for grammar-only use cases.

---

## Scope

In:
- New provider `arc.providers.llama_cpp` with two operating modes:
  - `mode: compat` (default) — delegates to `OpenAICompatProvider`
  - `mode: grammar` — uses `/completion` with a GBNF tool-use schema
    auto-generated from `LLMRequest.tools`
- Auto-generated GBNF: turn arc's JSON-Schema tool inputs into a grammar
  the model is *forced* to comply with
- Preflight `/health` check (warn-only, like Ollama)
- Pricing entry: `llama_cpp/*` → $0 (via 0014's `_patch_local_models`)
- Capability flags identical to Ollama's compat plumbing
- `defaults.py` commented example
- Reuse 0014's `openai_compat` shim — no duplication

Out (deferred):
- Speculative decoding / draft-model config exposure.  Pass-through via
  `provider.params.draft_model` works for compat mode; native mode could
  surface it as a first-class knob in a follow-up.
- Multi-slot server (`-np N` parallel slots).  Each session uses one
  slot; the runtime doesn't manage slot affinity.
- `/v1/embeddings` for the future RAG-on-events plugin.  Cleanly
  separable; design when the RAG phase happens.
- Auto-download of .gguf files.  Out of scope; user runs llama.cpp's
  own download tooling.

---

## Architecture

```
src/arc/providers/
  llama_cpp/
    __init__.py             ← re-exports LlamaCppProvider
    provider.py             ← mode dispatcher; compat path delegates
    grammar.py              ← JSON-Schema → GBNF compiler
    native_client.py        ← thin httpx wrapper around /completion
tests/unit/test_llama_cpp_provider.py
tests/unit/test_grammar_compiler.py
tests/integration/test_llama_cpp_live.py
```

`provider.py`:

```python
class LlamaCppProvider:
    name = "llama_cpp"

    def __init__(self, cfg: ProviderConfig):
        self._cfg = cfg
        self._mode = cfg.params.get("mode", "compat")
        if self._mode == "compat":
            self._impl = OpenAICompatProvider(
                base_url=cfg.base_url or "http://localhost:8080/v1",
                api_key=os.environ.get(cfg.api_key_env or "LLAMA_CPP_API_KEY", "sk-no-key"),
                model=cfg.model or "default",
                retry=cfg.retry,
                params={k: v for k, v in cfg.params.items() if k != "mode"},
                capabilities=CompatCapabilities(tool_use=True, parallel_tool_calls=False),
            )
        elif self._mode == "grammar":
            self._impl = _NativeGrammarPath(cfg)
        else:
            raise ValueError(f"llama_cpp mode must be 'compat' or 'grammar', got {self._mode!r}")

    def chat(self, req: LLMRequest) -> LLMResponse:
        return self._impl.chat(req)
```

### Compat mode

Nothing new.  Inherit 0014's `OpenAICompatProvider`, point at
`http://localhost:8080/v1` by default, set `parallel_tool_calls=False`
(llama-server's compat mode handles parallel tool calls inconsistently
across model templates — disable by default; users can re-enable in
config if their template supports it).

This is the recommended path for any model with a well-trained tool-use
chat template (Llama 3.1+ instruct, Hermes 3, Qwen 2.5 instruct).

### Grammar mode — the interesting part

For small or quirky models where tool-call output is unreliable, we:

1. Compile a GBNF grammar that expresses "either a final text answer OR
   a JSON tool call matching one of the provided tools."
2. POST to `/completion` with `prompt=<formatted messages>`, `grammar=<the
   GBNF string>`, `n_predict=<max_tokens>`.
3. Parse the generated text deterministically — it's guaranteed to match
   the grammar, so a single-pass split between "text-answer prefix" and
   "tool-call JSON" works without any defensive parsing.

#### Grammar shape

Top-level alternative:

```gbnf
root ::= text-answer | tool-call

text-answer ::= "ANSWER:\n" [^\x00]+

tool-call ::= "TOOL:\n" tool-json
tool-json ::= tool-ls | tool-bash-exec | tool-web-search | …
```

Each `tool-X` rule is generated from that tool's `input_schema` — a
JSON-Schema-to-GBNF walker that handles `object`, `string`, `integer`,
`number`, `boolean`, `array`, `enum`, plus `required`.  About 200 lines.
Existing references (e.g. `llama.cpp/grammars/json.gbnf`) cover the JSON
primitives; we just emit the per-property structure.

```gbnf
tool-ls ::= "{ \"name\": \"ls\", \"input\": " ls-input " }"
ls-input ::= "{" ("\"path\": " string)? ("," "\"max_depth\": " integer)? "}"
```

#### Prompt formatting

The model needs to *know* the rules and tool list.  Append a fixed
postamble to the system prompt:

```
Reply EXACTLY in one of these two formats:

  ANSWER:
  <your reply>

  OR

  TOOL:
  {"name": "<tool>", "input": {<args>}}

Available tools:
- ls(path: str, max_depth: int): list directory entries
- bash_exec(command: str): run a bash command
- …
```

This is plain string concatenation in `_NativeGrammarPath.chat()` — no
hook needed; the modification is to the *outgoing* system prompt, which
plugins shouldn't see (they're operating on the arc-level request, this
is provider-level formatting).

#### Response translation

The generated string is guaranteed to start with either `ANSWER:` or
`TOOL:`.  Split on that and emit the appropriate `ContentBlock`:

- `ANSWER:...` → `[ContentBlock(type="text", text=rest.strip())]`,
  `stop_reason="end_turn"`
- `TOOL:...` → parse the JSON (guaranteed valid by grammar), build
  `ContentBlock(type="tool_use", tool_use_id=<ulid()>,
  tool_name=parsed["name"], tool_input=parsed["input"])`,
  `stop_reason="tool_use"`

Grammar mode does **not** support parallel tool calls.  The grammar allows
exactly one tool call per response.  Sequential tool-loop iteration is
unchanged — the runtime calls the provider in a loop already; grammar
mode just constrains each call to one tool.

#### `.raw` for grammar mode

`/completion` returns a JSON envelope with the generated text, timings,
slot info, sampler state, and token counts.  Capture verbatim:

```python
raw = httpx_response.json()  # already a dict
return LLMResponse(
    content=blocks,
    stop_reason=stop_reason,
    input_tokens=raw.get("tokens_evaluated", 0),
    output_tokens=raw.get("tokens_predicted", 0),
    raw=raw,                    # byte-faithful
)
```

Replay (mode 2) reconstructs from `raw["content"]` re-running the same
split/parse code — no network, fully deterministic.

---

## Config

```yaml
provider:
  name: llama_cpp
  model: ""                          # informational; llama-server has one model loaded
  api_key_env: LLAMA_CPP_API_KEY     # ignored unless server has --api-key
  base_url: http://localhost:8080/v1 # /v1 prefix for compat mode; native mode strips it
  timeout_seconds: 120
  retry:
    max_attempts: 3
    backoff_base_seconds: 1
    backoff_max_seconds: 8
  params:
    mode: compat                     # 'compat' | 'grammar'
    temperature: 0
    max_tokens: 4096
    # Compat-mode passes everything to openai SDK; native-mode passes to /completion
    # (top_p, top_k, repeat_penalty, mirostat, mirostat_tau, n_keep, n_probs, …)
```

Switching from Ollama to llama.cpp compat-mode is a name + base_url + port
change.  Switching to grammar mode is one additional `mode: grammar` line.

---

## Tool-call ID semantics

Grammar mode synthesizes its own `tool_use_id` (a new ULID per call)
because the model output doesn't include one.  The downstream
`role="tool"` message echoes that id back — which we never need to send
back to llama.cpp (the prompt is rebuilt fresh each turn from the
`pack_context`-filtered message list), so the id is internal-only.  Same
strategy Gemini uses (positional / synthesized) per the existing
provider-authoring guide.

In compat mode, `tool_use_id` comes from the OpenAI tool_calls payload
as usual.

---

## Preflight check

```python
try:
    resp = httpx.get(f"{base_url.rstrip('/v1')}/health", timeout=3)
    body = resp.json()
    if body.get("status") not in ("ok", "loading model"):
        log.warning("llama_cpp: /health returned %r", body)
    if body.get("status") == "loading model":
        log.info("llama_cpp: server is still loading the model; first call will block")
except (httpx.HTTPError, json.JSONDecodeError):
    pass  # let the first chat() surface a real error
```

Warn-only, same convention as 0014.

---

## Observability

Reuses 0014's events.  One small addition to `llm.call.completed.metadata`
when in grammar mode:

```json
{
  "input_tokens": 245,
  "output_tokens": 38,
  "llama_cpp_mode": "grammar",
  "llama_cpp_grammar_size_bytes": 1842,
  "llama_cpp_predicted_per_token_ms": 18.4
}
```

Cheap to add, useful when debugging "why is this slow" or "did the grammar
work" — visible in the log_writer one-liner.

No new event types.

---

## Recovery and failure modes

| Failure | Behavior |
|---|---|
| `llama-server` not running | Retry policy → after exhausting, `llm.call.failed` with "llama-server unreachable at http://localhost:8080 — start it with `llama-server -m <model>.gguf`" |
| Model still loading at first call | Retry policy covers it; users with very large models should bump `retry.max_attempts` |
| Grammar generation fails for a tool's schema | At provider construction, compile grammar from `req.tools` at first `chat()` call.  Schema features unsupported by the GBNF compiler (e.g., regex `pattern`, deeply nested `anyOf`) → log a clear error and refuse startup: "tool 'X' has a schema feature unsupported in grammar mode: pattern. Switch mode to 'compat' or simplify the schema." |
| Server runs out of slots | 503; retry policy.  Single-user setup typically has 1 slot — concurrent sessions would clash; sessions are serialized at the agent level anyway. |
| Context overflow | Server returns 400 with "context window exceeded"; surface verbatim.  sliding-window-context should prevent it in normal use. |
| Model emits incoherent text-answer (compat mode, no grammar) | Same as any other provider — the model's problem, not ours.  Grammar mode is exactly the escape hatch. |
| `.raw` parse failure | Defensive: log + raise `ProviderError`; bubbles to `llm.call.failed`. |

---

## Pricing

Reuses 0014's `_patch_local_models` — `llama_cpp/*` defaults to $0/$0.
TUI shows tokens, hides cost (toolbar shows `$0.00`).

---

## File layout

```
src/arc/providers/
  llama_cpp/
    __init__.py
    provider.py
    grammar.py
    native_client.py
src/arc/providers/__init__.py        ← "llama_cpp" case in build()
src/arc/defaults.py                  ← commented example block
src/arc/tui/pricing.py               ← already patched in 0014
tests/unit/test_llama_cpp_provider.py
tests/unit/test_grammar_compiler.py
tests/integration/test_llama_cpp_live.py
```

No new top-level deps — `httpx` is already a transitive dep.  The GBNF
compiler is pure Python; no library needed.

---

## Test plan

Unit (`test_grammar_compiler.py`):
1. Empty tool list → grammar with only `text-answer` branch
2. Single tool with string-only input
3. Tool with required + optional fields
4. Tool with enum
5. Tool with integer/number/boolean
6. Tool with array of strings
7. Tool with nested object (one level)
8. Unsupported feature (regex pattern) → raises `GrammarCompileError`
9. Generated grammar passes basic GBNF self-check (parser sanity)

Unit (`test_llama_cpp_provider.py`):
1. Mode dispatch: `compat` delegates to OpenAICompatProvider; `grammar`
   uses native path
2. Compat mode is otherwise covered by 0014's `test_openai_compat.py`
3. Grammar mode: stubbed `/completion` returning known text → correct
   `LLMResponse` (text-answer path)
4. Grammar mode: stubbed `/completion` returning tool-call text → correct
   `LLMResponse` with synthesized `tool_use_id`
5. `.raw` populated from `/completion` JSON
6. Preflight: server up + model loaded → no warnings; server down → no
   crash, no log entry
7. Postamble injection: grammar mode appends the tool list + format
   instructions to system prompt; compat mode does not

Integration (`test_llama_cpp_live.py`):
1. Skip unless `LLAMA_CPP_HOST` env var set
2. Compat mode: tool-using turn end-to-end against the `ls` tool
3. Grammar mode: same turn, asserts grammar response parses cleanly and
   stop_reason is `tool_use`
4. Mode switch within fixture (two separate provider builds), confirm
   both paths function

Smoke:
- Start `llama-server -m models/llama-3.2-3b-instruct-q4_0.gguf --port 8080`
- `arc bootstrap`, set `provider.name: llama_cpp` + `mode: grammar`
- `arc run "list the files here"` — confirm tool fires; check
  `events.jsonl` `.raw` round-trips a replay

---

## State

Planned.

---

## Why ship two modes in one provider rather than two providers

Option A: separate `llama_cpp_compat` and `llama_cpp_grammar` provider
names.  Cleaner conceptually but doubles registration boilerplate and
forces users to know which mode their server speaks before configuring.

Option B (this doc): one `llama_cpp` provider with a `params.mode` switch.
Discovery is config-driven; users can flip modes by changing one line;
both paths share the preflight + pricing + base_url plumbing.

B wins on ergonomics.  The `_impl` indirection inside the provider is the
small price.
