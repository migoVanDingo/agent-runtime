# 0016 — Plan-and-Execute: Phase 1 — Plan Schema

## Goal

Define the `Plan` and `Step` data structures that flow between the planner,
executor, and synthesizer. These are the canonical types for the entire
plan-and-execute system.

---

## Files

### New: `src/planning/__init__.py`
Empty package init.

### New: `src/planning/schema.py`

**`ActionType`** — string enum of valid step action categories.
Maps directly to toolset names so the executor can use it as a routing hint.

```
analysis | file_io | shell | crypto | conversation
```

**`StepStatus`** — string enum tracking execution state.

```
pending | running | completed | error
```

**`StepFlags`** — dataclass for future runtime control fields.
Inert for now — executor does not act on these until retry/replan is implemented.

```python
retry: bool     # request retry on failure
escalate: bool  # escalate to a stronger model
defer: bool     # defer this step to a later turn
```

**`Step`** — one unit of work in the plan.

| Field | Type | Set by |
|---|---|---|
| `step` | int | planner |
| `description` | str | planner |
| `action_type` | ActionType | planner |
| `status` | StepStatus | executor code |
| `result` | str \| None | executor code |
| `error` | str \| None | executor code |
| `flags` | StepFlags | planner |

**`Plan`** — the full plan artifact.

| Field | Type | Notes |
|---|---|---|
| `original_query` | str | preserved for synthesizer |
| `steps` | list[Step] | ordered |
| `requires_synthesis` | bool | False for single-step conversational plans |

**Serialization helpers:**
- `Step.to_dict()` / `Step.from_dict()` — for JSON round-trips with the model
- `Plan.to_dict()` / `Plan.from_dict()` — same
- `Plan.summary()` — returns completed steps + results as a plain string
  for the synthesizer prompt

---

## Notes

- `ActionType` and `StepStatus` inherit from `str` so they serialize naturally
  to JSON without custom encoders
- `Step.from_dict()` is tolerant of missing optional fields — the model may
  omit `result`, `error`, or `flags` and defaults kick in
- No external dependencies — stdlib `dataclasses` + `enum` only
