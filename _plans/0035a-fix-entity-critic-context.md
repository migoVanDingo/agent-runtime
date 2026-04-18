# 0035a — Fix: Entity Critic User Message Authority

**Date**: 2026-04-18
**Status**: Implemented
**Parent**: 0035

## Problem

`EntityCritic.correct()` received only prior conversation history as context.
When a user introduced a new filename in their current message (e.g. "write
it to `_tests/grok-proc-output.txt`"), the critic saw the planner correctly
using that name, but since the name wasn't in prior history, it "corrected"
it away — replacing it with the most recently mentioned `.md` file from
history. The user's explicit intent was discarded.

Observed in session SES01KPGBW02SEP98G9ZDV5G9RDZV:
```
entity critic: step 2 path '_tests/grok-proc-output.txt' → '_tests/grok-proc-analysis-deep.md'
```

## Fix

Added `user_message: str | None = None` parameter to `EntityCritic.correct()`.

Entities extracted from `user_message` are treated as **authoritative**:
- They are added to the candidate pool (so the planner can use them)
- They are placed in `auth_paths` / `auth_files` sets
- Any plan reference that matches an authoritative entity is **skipped** — never corrected

The invariant: prior history provides candidates for resolving ambiguous
references ("the same file", "that binary"). The current user message
provides ground truth for new entities the user just named.

## Changes

- `src/runtime/entity_critic.py` — `correct()` accepts `user_message`, extracts authoritative entities, skips correction for those
- `src/agent.py` — passes `user_message` to `entity_critic.correct()`
