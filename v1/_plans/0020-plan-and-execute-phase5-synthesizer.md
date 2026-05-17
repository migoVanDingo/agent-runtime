# 0020 — Plan-and-Execute: Phase 5 — Synthesizer

## Goal

Implement the Synthesizer — the final stage of the plan-and-execute pipeline.
Takes the completed plan and produces a natural, conversational response for
the user. Internal step details, failures, and tool outputs are abstracted away.

---

## Files

### Updated: `src/planning/prompts.py`

Two new prompt strings added:

**`SYNTHESIS_SYSTEM_PROMPT`** — instructs the model to act as a conversational
assistant summarizing work that was completed on the user's behalf. Never
mention step numbers, tool names, or internal failures unless directly
relevant to the user's goal.

**`SYNTHESIS_USER_TURN`** — templated with `{original_query}` and `{summary}`.
The summary comes from `Plan.summary()` — completed steps and their results
only. Failed steps appear as "failed" without detail.

### New: `src/planning/synthesizer.py`

**`Synthesizer`** class. Receives a provider as a dependency.

**`synthesize(plan: Plan) -> str`**
1. Create a fresh `Messenger`
2. Format `SYNTHESIS_USER_TURN` with `plan.original_query` and `plan.summary()`
3. Call provider with `tools=[]` and `SYNTHESIS_SYSTEM_PROMPT`
4. Return the text response

No JSON parsing, no validation, no retry — this is a pure conversational
generation call. If it returns empty text, the caller handles the fallback.

---

## Notes

- The synthesizer never sees the full tool call history — only the plan summary
- `Plan.summary()` already filters to completed steps with results, so the
  synthesizer prompt stays clean and focused
- `requires_synthesis: false` on the plan is the caller's signal to skip this
  stage entirely — the synthesizer itself has no opinion on that
- Fresh `Messenger` per call — no state accumulation
