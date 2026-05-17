# 0018 — Plan-and-Execute: Phase 3 — Planning Gate

## Goal

Implement the lightweight heuristic that decides whether a user message
warrants a planning pass before execution. No LLM call — pure Python.

---

## Files

### Updated: `config.yml`

New `planning` section:

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

### Updated: `src/config.py`

New `PlanningGateConfig` and `PlanningConfig` dataclasses.
`AppConfig` gains a `planning: PlanningConfig` field.

### New: `src/planning/gate.py`

`PlanningGate` class with a single `should_plan(message) -> bool` method.

**Logic (both conditions must be true):**
1. `len(message) >= config.planning.gate.min_message_length`
2. Any indicator word appears as a whole token in the lowercased message

Multi-word indicators (e.g. "and then") are checked as substrings after
single-word token matching — order matters, multi-word checked first to
avoid partial matches on the single-word pass.

---

## Notes

- Gate reads from `config.planning.gate` so thresholds are tunable without
  code changes
- Returns `False` for short messages and purely conversational inputs —
  "hi", "thanks", "what is X?" never trigger the planner
- A message that passes the gate is not guaranteed to need a complex plan —
  the planner may still return a single-step plan, which is valid
