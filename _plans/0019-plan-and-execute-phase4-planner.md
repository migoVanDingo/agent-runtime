# 0019 — Plan-and-Execute: Phase 4 — Planner

## Goal

Implement the Planner — a self-contained component that takes a user message
and returns a validated `Plan` object. Uses its own ephemeral `Messenger`
instance. Its internal deliberation is never exposed to the executor.

---

## Files

### New: `src/planning/prompts.py`

Two prompt strings:

**`PLANNING_SYSTEM_PROMPT`** — sets the planner's role. Instructs it to return
ONLY raw JSON (no markdown, no explanation). Lists valid `action_type` values
with descriptions. Templated with `{max_steps}`.

**`PLANNING_USER_TURN`** — injected as the user message. Contains:
- The exact JSON schema the model must return
- A concrete two-step worked example
- The actual user task at the bottom, templated with `{user_message}`

Keeping the schema and example in the user turn (not just the system prompt)
puts them in the model's immediate context during generation — critical for
small model compliance.

### New: `src/planning/planner.py`

**`Planner`** class. Receives a provider as a dependency (same provider used
by the executor — configurable model support deferred).

**`plan(user_message) -> Plan | None`**
1. Create a fresh `Messenger` for this planning call
2. Format prompts with `user_message` and `max_steps` from config
3. Call provider with `tools=[]` (no tools during planning)
4. Extract text from response
5. Parse + validate via `_parse()`
6. On failure, retry once if `config.planning.retry_on_invalid` is set —
   appends the error and asks the model to try again
7. On second failure, return `None` (caller falls back to direct execution)
8. Override `original_query` with the actual `user_message` — don't trust
   the model to copy it exactly

**`_parse(raw) -> Plan | None`**
- Strips markdown code fences if present (` ```json ... ``` `)
- Parses JSON — returns `None` on `JSONDecodeError`
- Validates required fields: `steps` is a non-empty list, each step has
  `step`, `description`, `action_type`
- Validates `action_type` values against `ActionType` enum
- Calls `Plan.from_dict()` — returns `None` on any exception

---

## Notes

- `Messenger` is created fresh per `plan()` call — planner state does not
  accumulate across turns
- `tools=[]` is passed to the provider — the planner never calls tools
- The planner logs warnings on parse/validation failures at INFO level
  so they appear in the log file for debugging
- Returning `None` is not an error — the agent treats it as a signal to
  skip planning and go direct
