# 0021 — Plan-and-Execute: Phase 6 — Wire Agent

## Goal

Orchestrate all three stages (gate → planner → executor → synthesizer) inside
`agent.py`. The direct execution path remains intact as the fallback.

---

## Changes

### Updated: `src/agent.py`

**New components instantiated in `__init__`:**
- `PlanningGate` — heuristic check
- `Planner(provider)` — produces plans
- `Synthesizer(provider)` — produces final responses

**`call(user_message)`** — entry point, now branches:
```
gate.should_plan()?
  yes → spinner "Planning..." → planner.plan()
          → plan returned  → _execute_plan()
          → None returned  → spinner update to "Thinking..." → _run_loop()
  no  → spinner "Thinking..." → _run_loop()
```

**`_execute_plan(plan)`** — per-step orchestration:
1. For each step:
   - Spinner: `Step N/M — <description (40 chars)>`
   - Add transition user message for steps 2+ (`"Step N complete. Now execute step N+1: ..."`)
     to maintain alternating user/assistant message structure
   - Route on `step.description` — more precise than routing on the full user message
   - Guarantee `step.action_type` toolset is always in the selected set
   - `conversation` steps get `tools=[]`
   - Build step-aware system prompt via `_step_system()`
   - Run `_run_step()` — capture text result
   - Mark `step.status = COMPLETED`, `step.result` (truncated to 500 chars)
2. Stop spinner
3. If `plan.requires_synthesis`: run synthesizer → return response
4. Else: return last step's result directly

**`_run_step(step, n_total, tools, system)`** — ReAct loop for one step.
Same structure as the existing loop. Spinner shows `Running <tool>...` during
tool calls, restores step message after. Returns text on `end_turn`.

**`_run_loop(user_message, system)`** — existing direct execution loop,
factored out. Stops spinner on `end_turn`.

**`_step_system(plan, current_step)`** — builds a step-aware system prompt:
```
[base system prompt]

You are executing a multi-step plan:
  ✓ Step 1: <description>   ← completed
  → Step 2: <description>   ← current
    Step 3: <description>   ← pending

Currently executing Step 2 of 3: <description>
Complete this step using the available tools, then stop.
```

---

## Message structure during plan execution

```
user:      "original user message"
assistant: [tool calls — step 1]
user:      [tool results]
assistant: "step 1 result text"
user:      "Step 1 complete. Now execute step 2: ..."
assistant: [tool calls — step 2]
user:      [tool results]
assistant: "step 2 result text"
...
```

Alternating user/assistant structure is maintained throughout.

---

## Notes

- Step status is marked COMPLETED after `_run_step` returns, before the next
  step's system prompt is built — so completed markers (✓) are accurate
- Planning failures (planner returns None) fall through to direct execution
  with a spinner update rather than a stop+start cycle
- `step.result` is capped at 500 chars for the plan artifact — full output
  lives in the conversation history
