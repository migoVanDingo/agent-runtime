# Provider authoring guide

A provider in arc is a class that implements one method — `chat(req: LLMRequest)
-> LLMResponse` — translating arc's provider-agnostic types to/from a vendor
SDK. The runtime never sees provider-specific types.

This guide walks the contract. The two reference implementations are
[`gemini.py`](../src/arc/providers/gemini.py) (~340 lines) and
[`anthropic.py`](../src/arc/providers/anthropic.py) (~440 lines).

---

## 1. The Protocol

[`src/arc/providers/base.py`](../src/arc/providers/base.py):

```python
class LLMProvider(Protocol):
    name: str  # "gemini", "anthropic", ...

    def chat(self, req: LLMRequest) -> LLMResponse: ...
```

That's the whole interface. `LLMRequest` and `LLMResponse` are defined in
[`src/arc/runtime/hooks.py`](../src/arc/runtime/hooks.py).

---

## 2. The arc-side types

You translate to and from these. They are deliberately small and frozen.

### `LLMRequest` (input)

```python
@dataclass(frozen=True)
class LLMRequest:
    messages: list[Message]  # full conversation
    system: str              # system prompt (separate from messages)
    tools: list[ToolSpec]    # available tools, JSON-Schema input_schema
    model: str               # model id ("claude-haiku-4-5", "gemini-3.1-flash-lite-preview")
    params: dict[str, Any]   # temperature, max_tokens, etc.
```

### `Message`

```python
@dataclass(frozen=True)
class Message:
    role: str         # "user" | "assistant" | "tool"
    content: Any      # str for user/assistant text; list[ContentBlock] for tool calls/results
    name: str | None  # tool name for role=tool
```

### `ContentBlock`

```python
@dataclass(frozen=True)
class ContentBlock:
    type: str  # "text" | "tool_use" | "thinking"
    text: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict | None = None
    metadata: dict | None = None  # provider-specific data to echo back
```

### `LLMResponse` (output)

```python
@dataclass(frozen=True)
class LLMResponse:
    content: list[ContentBlock]
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | other
    input_tokens: int
    output_tokens: int
    raw: dict[str, Any] = field(default_factory=dict)  # ← critical
```

---

## 3. The byte-fidelity contract

This is the most important rule:

> **Every `LLMResponse` you return MUST include `.raw` populated with the
> provider's full response, serialized to a JSON-faithful dict.**

The recorder persists `.raw` verbatim. The replay engine reconstructs from
it without re-calling the API. If `.raw` doesn't round-trip, replay breaks.

The pattern for SDK-provided objects:

```python
# At the top of your provider, after the chat() call:
raw_response = sdk_client.messages.create(...)
raw_dict = raw_response.model_dump(mode="json")  # Pydantic-backed SDKs (Anthropic, OpenAI)
# or
raw_dict = sdk_response.to_dict()                # google-genai
```

The replay test in `tests/integration/test_replay_byte_fidelity.py` will catch
you if you skip this. It re-runs a recorded session, asserts the events.jsonl
matches byte-for-byte. Anything provider-side you don't capture in `.raw`
becomes a divergence.

See [`_design/0002-foundation-phase1-gemini-sdk-byte-fidelity.md`](../_design/0002-foundation-phase1-gemini-sdk-byte-fidelity.md)
for the full rationale.

---

## 4. The translation step

Three translations happen in every `chat()`:

### a. arc → provider request

Convert `req.messages`, `req.system`, `req.tools` to whatever the SDK wants.
Anthropic and Gemini have very different shapes — that's fine, that's why this
layer exists.

Watch out for:
- **System prompt placement.** Anthropic takes `system=` as a top-level
  parameter. Gemini takes it via `system_instruction=` in `GenerateContentConfig`.
  Don't put it in `messages`.
- **Tool result format.** Anthropic wants `{"type": "tool_result",
  "tool_use_id": ..., "content": ...}` inside the user-role content list.
  Gemini wants `Part.function_response(name=..., response={"result": ...})`.
- **Tool-use ID semantics.** Gemini doesn't surface a stable tool-use id
  on its function_call parts the way Anthropic does — arc's Gemini provider
  uses positional matching (the Nth function_call → the Nth function_response).
  See `_design/0010-anthropic-provider.md` §3 for the divergence in detail.

### b. SDK call with retry

Wrap the SDK call in arc's retry policy from `req.params["retry"]` (which the
runtime fills from `config.provider.retry`). Use the existing
`arc.providers._retry_call()` helper if you can — it handles exponential
backoff with jitter on the retryable error classes.

### c. provider → arc response

Convert the SDK response object into an `LLMResponse`. Iterate the response's
content parts; for each, build a `ContentBlock`:

```python
blocks = []
for part in response.content:
    if part.type == "text":
        blocks.append(ContentBlock(type="text", text=part.text))
    elif part.type == "tool_use":
        blocks.append(ContentBlock(
            type="tool_use",
            tool_use_id=part.id,
            tool_name=part.name,
            tool_input=dict(part.input),
        ))
    elif part.type == "thinking":
        # Anthropic 3.7+/4+. Signature must be echoed back on follow-up turns.
        blocks.append(ContentBlock(
            type="thinking",
            text=part.thinking,
            metadata={"signature": part.signature} if part.signature else None,
        ))

return LLMResponse(
    content=blocks,
    stop_reason=_translate_stop_reason(response.stop_reason),
    input_tokens=response.usage.input_tokens,
    output_tokens=response.usage.output_tokens,
    raw=response.model_dump(mode="json"),
)
```

### Stop-reason normalization

arc uses a small canonical set: `end_turn`, `tool_use`, `max_tokens`. Map
provider-specific reasons into one of these where possible. Pass through
unknowns as-is — the runtime treats anything unrecognized as `end_turn`
for control-flow purposes.

---

## 5. Registration

Two places:

### a. Builder + registry in `arc/providers/__init__.py`

```python
from arc.providers.your_provider import YourProvider

_PROVIDERS = {
    "gemini": _build_gemini,
    "anthropic": _build_anthropic,
    "your-provider": _build_your_provider,  # ← add
}

def _build_your_provider(cfg: ProviderConfig) -> LLMProvider:
    api_key = os.environ.get(cfg.api_key_env)
    if not api_key:
        raise ProviderError(f"{cfg.api_key_env} not set in environment")
    return YourProvider(
        api_key=api_key,
        model=cfg.model,
        retry=cfg.retry,
        params=cfg.params,
    )
```

### b. Default entries in `defaults.py`

You don't have to make your provider the default — users can opt in via
`config.yml`. But you should add a `# Example:` comment block under
`provider:` so users can see the shape.

---

## 6. Optional features

### Thinking blocks (Anthropic 3.7+/4+)

If your provider supports extended-reasoning blocks, surface them as
`ContentBlock(type="thinking")` with the signature in `metadata`. On
follow-up turns, echo the signature back in the assistant message — the
provider rejects the request if you don't. See
[`src/arc/providers/anthropic.py`](../src/arc/providers/anthropic.py)
`_assistant_blocks()` for the pattern.

The TUI auto-renders thinking blocks (dim italic under a `◇ thinking` glyph)
when `tui.show_thinking` is true. You don't need to do anything extra in
the provider.

### Vendor-specific signatures on tool calls

Gemini 3+ requires echoing back a `thought_signature` on each function-call
part. arc handles this via `ContentBlock.metadata` — the Gemini provider
stores the signature there and re-attaches it on the next turn. Use the
same pattern for any provider that demands round-tripped metadata.

---

## 7. Testing providers

Two test files per provider:

### Unit tests (`tests/unit/test_<provider>.py`)

Stub the SDK. Verify:
- Request translation produces correct shapes (system prompt placement, tool
  schemas, message roles)
- Response translation handles every block type
- Tool-result encoding is correct
- Retry policy is honored on retryable errors
- The `raw` field is populated

### Integration tests (`tests/integration/test_<provider>_live.py`)

Real API. Auto-skip if the API key env var isn't set:

```python
pytestmark = pytest.mark.skipif(
    not os.environ.get("YOUR_PROVIDER_API_KEY"),
    reason="no YOUR_PROVIDER_API_KEY",
)
```

Test a complete tool-using turn end-to-end and assert byte-faithful replay.

---

## 8. What not to do

- **Don't emit events.** The runtime wraps your `chat()` call and emits
  `llm.call.started` / `llm.call.completed` / `llm.call.failed` for you.
- **Don't mutate the request.** Hooks (`before_llm_call`) mutate. Providers
  just translate and send.
- **Don't catch SDK errors silently.** Let retry policy handle transient
  failures; bubble auth errors so the runtime can surface them.
- **Don't add provider-specific knobs to `RuntimeConfig`.** They go in
  `provider.params` and propagate through `LLMRequest.params`.

---

## 9. Quick reference: the existing providers

| File | LOC | Notes |
|---|---|---|
| [`gemini.py`](../src/arc/providers/gemini.py) | ~340 | `google-genai` SDK; positional tool_use_id; `thought_signature` round-trip |
| [`anthropic.py`](../src/arc/providers/anthropic.py) | ~440 | `anthropic` SDK; native tool_use_id; thinking-block signature echo |

Both are heavily commented. Read them in this order: `base.py` (the contract),
`gemini.py` (the simpler case), then `anthropic.py` (the case with thinking
blocks and richer block types).
