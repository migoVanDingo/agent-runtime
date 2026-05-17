# 0061 - Runtime Refactor Phase 6: Provider Capabilities And JSON Extraction

## Goal

Start consolidating provider/schema contracts without forcing all LLM decision components to migrate at once.

## Implemented

- Added `providers.capabilities.ProviderCapabilities`.
- Added default `BaseProvider.capabilities`.
- Declared Anthropic capabilities:
  - tool use supported,
  - structured JSON schema not yet supported through this provider wrapper.
- Declared OpenAI-compatible capabilities:
  - tool use supported,
  - structured JSON schema supported,
  - parallel tool calls supported.
- Added `runtime.json_extract.extract_json()`.
- Updated critic JSON extraction to delegate to the shared helper.
- Added unit coverage for fenced JSON and JSON embedded in surrounding text.

## Behavior Notes

This is a scaffolding phase. Native structured output remains unchanged:

- OpenAI-compatible providers still use `response_format=json_schema` when callers pass `json_schema`.
- Anthropic still ignores `json_schema` for now.

The shared JSON extractor is a fallback path, not the target end state.

## Remaining Work

- Migrate monitor, classifier, planner fallback parsing, routing, and importance parsing to `extract_json()`.
- Add Anthropic schema-forcing through a single synthetic tool call.
- Make planner/router/critic select behavior based on `provider.capabilities`.
- Add provider conformance tests with fake providers.

## Verification

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src
```
