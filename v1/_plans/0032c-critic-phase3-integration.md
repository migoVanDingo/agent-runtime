# 0032c — Plan Critic Phase 3: Integration

**Date**: 2026-04-15
**Status**: Implemented
**Parent**: 0032

## Changes

### 1. `agent.py` — Critic wired into plan flow
After structural validation passes, the critic reviews the plan:
```
Planner → Validator → Critic → (if challenged) → Planner.revise → Validator → Execute
```

Flow:
- If critic returns `APPROVED`: proceed to execution
- If critic returns `CHALLENGED`: format challenges, send to `planner.revise()`
- If revision succeeds: re-validate the revised plan, then execute
- If revision fails (returns None): log and fall back (original plan is discarded since critic found issues)
- One round only — no infinite challenge loops

### 2. `planning/planner.py` — Added `revise()` method
New method `revise(plan, challenges_text) -> Plan | None`:
- Receives the original plan and formatted critic challenges
- Sends a revision prompt that:
  - Shows the original plan
  - Lists each challenge
  - Demands specific justification for kept steps: "Name the specific fact the tool reveals"
  - Explicitly rejects vague defenses
- Parses the response as a new Plan
- Preserves `original_query` from the original plan

### 3. Spinner updates
- Shows "Reviewing plan..." during critic review
