# Phase 3: Classifier Workflow Hint (Option 1)

## What Was Built

The intent classifier now returns an optional `workflow_hint` alongside `mode` and `risk`. Workflow descriptions are injected into the classifier's system prompt at call time from `ALL_WORKFLOWS` — no manual maintenance. When the classifier identifies a workflow match, `agent.py` uses it before falling back to regex or the LLM planner.

## Changes

### `runtime/prompts.py` — `CLASSIFIER_SYSTEM_PROMPT`
- Changed from static string to template with `{workflow_descriptions}` placeholder
- Added fourth output field: `"workflow": <name> | null`
- Added workflow matching guidelines: match on intent and semantics, not keywords
- Added example: `"create a C program exactly like this binary"` → `"deep-disassembly"`
- Uses `{{` / `}}` escaping for literal braces in JSON examples

### `runtime/classifier.py`
- `classify()` now accepts `workflow_descriptions: list[tuple[str, str]] | None = None`
- Builds `{workflow_descriptions}` block and formats the system prompt at call time
- `_parse()` extended to extract and validate the `workflow` field
  - Unknown workflow names are discarded with a log line
  - Returns `(mode, risk, reason, workflow_hint)`
- `classify()` logs `workflow hint: {name}` when a hint is returned

### `workflows/base.py`
- `generate_plan(match)` signature changed to `generate_plan(match: re.Match | None, ...)`
- Docstring notes: workflows that require regex groups should raise `ValueError` when called with `match=None`

### `workflows/templates.py`
- `AnalyzeAndWrite.generate_plan`: raises `ValueError` if `match is None`
- `ReadModifyWrite.generate_plan`: raises `ValueError` if `match is None`
- `HashAndReport.generate_plan`: raises `ValueError` if `match is None`
- `DeepDisassembly.generate_plan`: updated signature; does not use `match` — works correctly with `None`

### `agent.py`
- Passes `wf_descriptions = self.workflow_matcher.get_descriptions()` to `classify()`
- New routing logic (before existing regex match):
  1. If `classification.workflow_hint` is set, look up workflow by name
  2. Try `workflow.try_match(message)` — if it matches, use it (`routing_path = "classifier_hint"`)
  3. If regex didn't confirm, try `workflow.generate_plan(None, message)` in a try/except
     - Success → use it (`routing_path = "classifier_hint_direct"`)
     - `ValueError` or any exception → log and fall through to regex
  4. Regex match runs as before if hint path produced nothing (`routing_path = "regex"`)
- All paths log which routing path was used

## Key Behavior

- `DeepDisassembly` is the primary beneficiary: it doesn't need the match object, so "create a C program exactly like it" now routes correctly via classifier hint even though the regex doesn't match.
- Workflows that need regex groups (`AnalyzeAndWrite`, `ReadModifyWrite`, `HashAndReport`) raise `ValueError` when called without a match, which the agent catches and falls through from gracefully.
- Unknown workflow names returned by the classifier are discarded — guards against model hallucination.
