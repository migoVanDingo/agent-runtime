# 0070 — Phase F: Provider capabilities + Anthropic structured output

## Goal

`ProviderCapabilities` exists but no caller consults it. Anthropic still
ignores `json_schema`. Five hand-rolled JSON parsers are fragile. This phase
wires them up.

## Scope

- Implement Anthropic structured output via single-tool trick in
  `AnthropicProvider._chat_impl`: when `json_schema` is provided, declare a
  synthetic `respond` tool and force tool_choice to `respond`.
- Set `AnthropicProvider.capabilities.structured_json_schema = True`.
- Migrate five parsers to `json_extract` (shared fallback):
  - `runtime/monitor.py::_parse`
  - `runtime/classifier.py::WorkflowSelector._parse` (already done in Phase A)
  - `runtime/importance.py::_parse`
  - `planning/planner.py::_parse`
  - `runtime/utils.parse_routing_response` (inner JSON only)
- `runtime/critic.py::_extract_json` (already delegates to json_extract — no change needed)

## Files touched

`providers/anthropic.py`, `providers/capabilities.py`,
`runtime/monitor.py`, `runtime/importance.py`, `planning/planner.py`.

## Exit criteria

- `grep -rn "json.loads" src/runtime/ src/planning/` returns only
  `json_extract.py` and `artifact_store.py` (intentional).
- Tests: each migrated parser handles fenced JSON, bare JSON, and
  malformed JSON returning the safe default.
