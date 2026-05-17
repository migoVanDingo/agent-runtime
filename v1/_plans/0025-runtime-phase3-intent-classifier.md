# 0025 — Runtime Infrastructure Phase 3: Intent Classifier

## What

Replace the keyword-based `PlanningGate` with an LLM-based
`IntentClassifier` that decides whether a user message requires
multi-step planning or direct single-turn execution.

## Why

The old gate used keyword matching ("then", "after", "first", etc.) and
missed real multi-step requests like "analyze /bin/pwd and write a
summary to results.md" because those words weren't present. It also had
no conversation awareness — it couldn't distinguish a fresh complex
request from a simple follow-up.

## Changes

### New files

- **`src/runtime/classifier.py`** — `IntentClassifier` class:
  - `classify(message, history) -> "plan" | "direct"`
  - Uses a separate provider instance (from `get_runtime_provider()`) —
    can be gpt-4o-mini, a small Ollama model, or the main model
  - Sees the last N messages of real conversation history (configurable
    via `runtime.intent_classifier.context_window`, default 6)
  - Returns "direct" on parse failure — safe fallback
  - Ephemeral Messenger (same pattern as Planner)

- **`src/runtime/prompts.py`** — prompt templates:
  - `CLASSIFIER_SYSTEM_PROMPT`: role, JSON schema, guidelines for
    plan vs direct, four concrete examples including follow-up detection
  - `CLASSIFIER_USER_TEMPLATE`: recent context + current message
  - Also includes `MONITOR_SYSTEM_PROMPT` and `MONITOR_USER_TEMPLATE`
    (for phase 5, defined here to keep all runtime prompts together)

### Modified files

- **`src/agent.py`**:
  - Imports: `PlanningGate` → `IntentClassifier`, added `get_runtime_provider`
  - `__init__`: `self.gate = PlanningGate()` → `self.classifier = IntentClassifier(get_runtime_provider())`
  - `call()`: replaces `self.gate.should_plan(user_message)` with
    `self.classifier.classify(user_message, history)`. The classifier
    receives conversation history excluding the just-added user message.
    Spinner shows "Classifying..." during the LLM call.
  - Log banner: `── Intent classification ──` appears before `── Planning ──`
    or `── Direct execution ──`

## Conversation awareness

The classifier sees the last 6 messages (configurable) formatted as
concise previews:

```
Recent conversation:
[user]: analyze /bin/pwd and give me a summary...
[assistant]: I'll analyze the binary... [used tools: read_file, run_command]
[tool results: 3 result(s)]
[assistant]: Here's the analysis of /bin/pwd...

Current message: what about /bin/cat?
```

This prevents the failure case where a conversational follow-up was
incorrectly routed to the planner.

## What does not change

- `PlanningGate` file still exists (not deleted yet — cleanup in phase 7)
- Planner, Synthesizer, executor logic — unchanged
- `planning.gate` config section — still present, just unused
