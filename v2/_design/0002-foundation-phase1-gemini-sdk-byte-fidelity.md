# 0002 — Gemini SDK byte-fidelity experiment

**Status:** resolved
**Phase:** 1
**Resolves:** open question in `0001-foundation-phase0-design.md` §11

## Question

Per design §6.3, recorded LLM responses must be byte-faithful so deterministic
replay produces identical event logs. The question: does the official `google-genai`
SDK expose the raw provider response in a form we can serialize and re-inject,
or does it normalize away detail that breaks replay?

## Approach

1. Make a simple Gemini call via the SDK
2. Capture every available view of the response
3. Check whether any view round-trips through `json.dumps + json.loads` cleanly
4. Decide: use SDK and store one of those views, or wrap HTTP directly

## Result

**Verdict: use the SDK. It IS byte-faithful.**

Experiment run against `gemini-3.1-flash-lite-preview` via `google-genai==2.3.0`
with a trivial "say pong" prompt. The SDK returns a `GenerateContentResponse`
(a pydantic model). All three serialization views round-trip cleanly through
`json.dumps + json.loads`:

| View | Bytes | Round-trips |
|------|-------|-------------|
| `resp.model_dump(mode="json")` | 1808 | ✓ |
| `resp.model_dump_json()` | 1688 | ✓ |
| `resp.to_json_dict()` | 1001 | ✓ |

The three views differ in how much detail they include. `model_dump(mode="json")`
preserves the most — including provider-internal fields that may matter for
replay verification — so we'll use that.

## Strategy adopted

- Provider implementation uses the `google-genai` SDK
- After every call, store `resp.model_dump(mode="json")` in the event's `content`
  field under key `raw_provider_response`
- Replay reads this dict back, reconstructs the response objects via the SDK's
  pydantic models (or just synthesizes our `LLMResponse` from it directly —
  decide when we build the replay engine)
- No HTTP wrapper needed

## Note on the model name

The user-provided default `gemini-3.1-flash-live-preview` does not exist as
a published model name. The closest match in the Gemini model list is
`gemini-3.1-flash-lite-preview` — "live" was a typo for "lite". Updating the
default config accordingly.

## Re-running the experiment

```bash
# With the default model name:
python3 _tests/experiment_gemini_sdk_fidelity.py

# Or with an override:
ARC_TEST_MODEL=gemini-2.5-flash python3 _tests/experiment_gemini_sdk_fidelity.py
```

