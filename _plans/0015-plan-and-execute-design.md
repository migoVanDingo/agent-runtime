# 0015 — Plan-and-Execute Agent: Design

## Overview

Introduce a three-stage execution model for complex tasks:

```
user message
  → [gate] is planning needed?
      → no  → executor (existing loop)
      → yes → planner → executor (per-step) → synthesizer → user response
```

Each stage is a distinct component with its own concerns. The plan is a
structured artifact produced by the planner, owned and updated by the
executor code (not the model), and consumed by the synthesizer.

---

## Motivation

The current single-loop executor has two compounding problems:

1. **Routing on mixed-intent messages fails** — "analyze /bin/ls and write a
   summary" selects only one toolset because the message has multiple signals.
   Per-step routing on a decomposed plan solves this cleanly.

2. **Small models lose track of multi-step tasks** — without an explicit plan,
   models like Nemo attempt everything in one turn, skip steps, or forget the
   write-back entirely. A structured plan gives the executor a clear unit of
   work per call.

---

## Architecture

### Stage 0 — Planning Gate

A lightweight Python heuristic. No LLM call. Checks:
- Message length (short messages are almost never multi-step)
- Presence of multi-step indicator words: "then", "after", "next", "finally",
  "first", "and then", "once you", "followed by"

Returns `True` (plan needed) or `False` (go direct to executor).

---

### Stage 1 — Planner

A self-contained component with its own `Messenger` instance (separate from
the main conversation). Its only job is to produce a validated plan.

**Model:** configurable via `config.yml`. Defaults to the same model as the
executor. In production a stronger model can be used for planning only.

**Call structure:**
- System prompt: planner role + instruction to return only valid JSON
- User turn: original user message + full plan schema + concrete example

**Output:** a `Plan` object (list of `Step` dataclasses) parsed from the
model's JSON response.

**Validation:** the planner validates the response against the schema. On a
single violation (malformed JSON, missing required fields) it retries once
with the error appended to the user turn. If the retry fails, fall back to
direct executor with no plan.

**Messenger lifetime:** ephemeral. The planner's internal deliberation is
discarded after the plan is produced. Only the plan artifact is passed forward.

---

### Stage 2 — Executor

The existing ReAct loop, extended to work per-step.

**Context injection:** before the first step, the plan is injected into the
main conversation as a system-style context message:
```
"Here is the plan you are executing: [serialized plan]"
```
The executor knows the full plan but operates on one step at a time.

**Per-step routing:** `router.select()` is called with the step's `description`
field (not the original user message). This gives precise toolset selection —
each step only sees the tools it needs.

**Status tracking:** after each step's execution turn completes, the executor
code (not the model) updates the step:
- `status`: `"completed"` or `"error"`
- `result`: truncated summary of the tool output or LLM response
- `error`: error string if applicable

The model never writes to the plan artifact.

**History:** full conversation history carries through all steps for now.
This will be revisited when multi-agent orchestration is introduced.

---

### Stage 3 — Synthesizer

A final LLM call after all steps complete. Produces the user-facing response.

**Input:**
- Original user message
- A summary of what was accomplished (completed steps + key results)
- Failures are abstracted or omitted — the user has a conversation, not a
  build log

**Output:** a natural, conversational response. The synthesizer does not know
about internal step failures unless they are relevant to the user.

**Note:** this is also a separate `Messenger` instance. The synthesizer's
prompt does not include the full tool call history.

---

## Plan Schema

```python
@dataclass
class Step:
    step: int                          # 1-indexed
    description: str                   # what the executor should do
    action_type: str                   # enum: analysis | file_io | shell | crypto | conversation
    status: str                        # pending | running | completed | error
    result: str | None                 # populated by executor code after completion
    error: str | None                  # populated by executor code on failure
    flags: dict                        # {retry: bool, escalate: bool, defer: bool}

@dataclass
class Plan:
    original_query: str
    steps: list[Step]
    requires_synthesis: bool           # False for single-step plans
```

---

## Routing Integration

Per-step routing replaces per-turn routing on the original message.

```
step.description → router.select() → toolset schemas → executor turn
```

The `action_type` field acts as a strong prior — it can be used to seed or
override the router result. For example: `action_type = "analysis"` guarantees
the analysis toolset is always included regardless of the router's keyword/
embedding signal.

---

## Config

New section in `config.yml`:

```yaml
planning:
  enabled: true
  model: null           # null = use same model as executor
  max_steps: 8
  retry_on_invalid: true
  gate:
    min_message_length: 20
    indicator_words:
      - then
      - after
      - next
      - finally
      - first
      - and then
      - once you
      - followed by
```

---

## Spinner / Progress Indicator

The user has no feedback between hitting enter and receiving a response.
A terminal spinner with concise status messages runs during all blocking
LLM and tool calls.

**Messages by stage:**
| Stage | Spinner text |
|---|---|
| Planning | `Planning...` |
| Executing step N of M | `Step N/M: <description>` (truncated to ~40 chars) |
| Direct execution (no plan) | `Thinking...` |
| Synthesizing | `Synthesizing response...` |

**Implementation:** a simple `Spinner` class using `threading.Thread` +
`itertools.cycle` over braille frames. No new dependencies. The spinner
runs on a daemon thread so it never blocks or hangs the process.

The spinner is only active when `--verbose` is off — when verbose logging
is streaming to the console, the spinner would interfere with log output.

---

## File Structure

```
src/planning/
  __init__.py
  gate.py          ← heuristic pre-check
  schema.py        ← Plan + Step dataclasses, JSON serialization
  prompts.py       ← planning system prompt + schema example for user turn
  planner.py       ← Planner class (own Messenger, produces Plan)
  synthesizer.py   ← Synthesizer class (own Messenger, produces final response)

src/ui/
  __init__.py
  spinner.py       ← Spinner class

src/agent.py       ← updated to orchestrate all three stages
src/main.py        ← passes verbose flag through for spinner suppression
src/config.yml     ← new planning section
src/config.py      ← new PlanningConfig dataclass
```

---

## Phases

| # | File | What |
|---|---|---|
| 1 | `src/planning/schema.py` | Plan + Step dataclasses, serialization |
| 2 | `src/ui/spinner.py` | Terminal spinner with status messages |
| 3 | `src/planning/gate.py` + config | Planning gate heuristic |
| 4 | `src/planning/prompts.py` + `planner.py` | Planner with validation + retry |
| 5 | `src/planning/synthesizer.py` | Synthesizer |
| 6 | `src/agent.py` | Wire all stages + spinner into the agent |

---

## What Does Not Change

- `StaticRouter` — used as-is, called with step description instead of user message
- `ToolRegistry` — unchanged
- `Messenger` — unchanged, instantiated separately for planner and synthesizer
- All tool implementations — unchanged
- `main.py` — unchanged
- Provider abstraction — unchanged

---

## Deferred

- **Retry / replan on step failure** — runtime infrastructure layer
- **Multi-agent delegation** — orchestrator hands steps to worker agents;
  step history isolation happens here
- **Plan persistence** — storing plans for auditing/replay (DAL layer)
- **Dynamic routing override via action_type** — action_type seeds the router
  but does not hard-override it yet; full integration deferred
