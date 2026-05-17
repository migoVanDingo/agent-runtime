# Phase 4: Targeted Fallback — WorkflowSelector (Option 3)

## What Was Built

`WorkflowSelector` — a focused single-call LLM router that fires only when both the classifier hint and regex matching have returned nothing. It asks one question: "does this request match any workflow?" The prompt is narrow and purposeful, without the distractions of mode/risk classification.

## Changes

### `runtime/prompts.py`
- Added `WORKFLOW_SELECTOR_SYSTEM_PROMPT`: focused prompt that lists available workflows and asks for a name-or-null match decision with a one-line reason
- Added `WORKFLOW_SELECTOR_USER_TEMPLATE`: minimal — just the user request

### `runtime/classifier.py`
- Added `WorkflowSelector` class:
  - `select(message, workflow_descriptions) -> str | None`
  - Builds prompt from descriptions, makes one LLM call (runtime provider, no tools)
  - `_parse()` validates returned name against valid workflow set, discards unknowns
  - Logs match/no-match with reason

### `agent.py`
- Imported `WorkflowSelector`
- Added `self.workflow_selector = WorkflowSelector(get_runtime_provider())` in `__init__`
- Added step 3 in routing logic (after regex miss):
  - Calls `self.workflow_selector.select(user_message, wf_descriptions)`
  - If name returned, looks up workflow and calls `generate_plan(None, message)` in try/except
  - Sets `routing_path = "fallback"` on success
  - Spinner shows "Routing..." during this call

## Trigger Condition

The fallback **only fires** when all of these are true:
- `mode == "plan"`
- `classification.workflow_hint` was `None`
- Regex match returned `None`

Never fires in direct mode. Never fires if classifier or regex already found a match.

## Routing Path Summary (all phases combined)

| Path | Condition | Extra LLM call? |
|---|---|---|
| `classifier_hint` | Classifier returned a hint + regex confirmed | No |
| `classifier_hint_direct` | Classifier returned a hint, regex missed, `generate_plan(None)` succeeded | No |
| `regex` | Classifier had no hint, regex matched | No |
| `fallback` | Classifier had no hint, regex missed, selector matched | Yes (one) |
| `(planner)` | All routing failed | Yes (planner call) |
