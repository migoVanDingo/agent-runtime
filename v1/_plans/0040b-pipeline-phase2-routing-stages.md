# 0040b — Pipeline Phase 2: Routing Stages + Utils Extraction

## What Was Implemented

Extracted shared helpers from `agent.py` into `src/runtime/utils.py` and
implemented `RoutingStage` and `DirectInlineStage` in
`src/runtime/stages/routing.py`.

`agent.py` is untouched — these stages exist alongside the current `call()`
and are not wired in yet.

## Files Created

### `src/runtime/utils.py`

Shared helpers previously scattered as module-level functions in `agent.py`.
Extracted so stage files can import them without circular dependencies.

Functions:
- `has_error_indicator(text)` — detects tool-level errors in results
- `banner(text)` — formats session log section headers
- `fmt_input(name, tool_input)` — compact log display for tool call inputs
- `fmt_result(result)` — compact log display for tool call outputs
- `build_routing_system(base_system, wf_descriptions)` — builds the combined
  agent system prompt + routing header instructions with workflow list injected
- `parse_routing_response(text, valid_workflows)` — extracts `<route>` header,
  returns `(ClassifierResult, remaining_text)`, defaults to `direct/low` on failure
- `is_clean_inline_answer(text)` — returns True if text is a genuine
  conversational response (no code fences, no action-promising phrases)
- `extract_entity_context(packed_messages)` — builds text-only view of message
  history excluding tool results (prevents false-positive entity critic candidates)

### `src/runtime/stages/__init__.py`

Empty package marker.

### `src/runtime/stages/routing.py`

**`RoutingStage`**

Makes the single combined API call. Writes `packed_messages`,
`classification`, `answer_text`, and `entity_context` into context.
Always returns `OK` — routing is fault-tolerant by design (defaults to
`direct/low` on any parse failure).

Dependencies injected at construction: `provider`, `context_mgr`,
`workflow_matcher`, `messenger`.

**`DirectInlineStage`**

Immediately follows `RoutingStage` in the pipeline. Checks if the model
produced a clean conversational inline answer:
- If yes: stores it as `context.response`, adds it to messenger history,
  returns `DONE` (pipeline short-circuits, no further stages run).
- If no (code fences, action phrases, or mode != "direct"): returns `OK`,
  pipeline continues.

Dependencies injected at construction: `messenger`.

## No Behavior Change

`agent.py` is untouched. The helpers still exist in `agent.py` in their
original form — they will be removed in Phase 7 (cutover). Both copies
are identical; the stage files use the `utils.py` versions.

## Next Phase

Phase 3 — implement `WorkflowMatchStage` and `PlanningStage`.
