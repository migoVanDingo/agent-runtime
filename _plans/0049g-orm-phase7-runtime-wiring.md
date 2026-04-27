# 0049g — ORM/DAL Phase 7: Runtime Wiring (Feature-Flagged Persistence)

**Status:** Implemented
**Phase:** 7 of 7

## What was built

### New files

| File | Purpose |
|---|---|
| `src/runtime/persistence.py` | `PersistenceWriter` — sync façade over async DAL; all public methods are no-ops when `ENABLE_SESSION_PERSISTENCE=false` |

### Modified files

| File | Change |
|---|---|
| `src/runtime/pipeline_context.py` | Added `db_session_id: str | None = None` field |
| `src/agent.py` | `Agent.call()` — creates session on entry, closes it on exit |
| `src/runtime/stages/execution.py` | `_execute_plan()` — records plan on start, records each step result after execution |

## PersistenceWriter API

```python
PersistenceWriter.enabled() -> bool
PersistenceWriter.start_session(query, model, provider) -> Optional[str]  # returns session_id
PersistenceWriter.record_plan(session_id, plan_index, query, steps, replan_reason) -> Optional[str]  # returns plan_id
PersistenceWriter.record_step(session_id, plan_id, step_index, action_type, description, tool, status, result, error, retry_count, importance_score)
PersistenceWriter.finish_session(session_id, total_steps, error)
```

All methods catch exceptions internally and log warnings — a DB failure never crashes the agent.

## Data flow

```
Agent.call(user_message)
  → PersistenceWriter.start_session()     → INSERT agent_session (status=active)
  → Pipeline.run(context)
      → ExecutionStage._execute_plan()
          → PersistenceWriter.record_plan()   → INSERT plan
          → [for each step]
              → tool execution
              → importance scoring
              → PersistenceWriter.record_step() → INSERT/UPDATE step
  → PersistenceWriter.finish_session()    → UPDATE agent_session (status=completed/error)
```

## Feature flag

```bash
# Off (default) — zero DB writes, zero overhead
ENABLE_SESSION_PERSISTENCE=false

# On — full persistence
ENABLE_SESSION_PERSISTENCE=true
AGENT_DB_URL=sqlite+aiosqlite:///./data/agent.db  # or postgres URL
```

## What's persisted

| Entity | Fields captured |
|---|---|
| AgentSession | query, model, provider, status, total_steps, completed_at, error |
| Plan | session_id, plan_index, original_query, steps_json |
| Step | plan_id, session_id, step_index, action_type, tool, status, result (1000 chars), error, retry_count, importance_score |
| Artifact | not yet wired — placeholder exists, wired in a future iteration |

## Live verification

With `ENABLE_SESSION_PERSISTENCE=true`:
```
Created session: SESS01KQ5X4AGQXN6EJRMD20BNS38S
Created plan:    PLAN01KQ5X4AH17Z8K4ME3HEG521S6
Created step:    STEP01KQ5X4AH9FMT2KRNXS9871WPM
Sessions in DB:  [('SESS01KQ5X4AGQXN6EJRMD20BNS38S', 'completed', 1)]
Plans in DB:     [('PLAN01KQ5X4AH17Z8K4ME3HEG521S6', 0)]
Steps in DB:     [('STEP01KQ5X4AH9FMT2KRNXS9871WPM', 'completed', 0.85)]
```

## Not yet wired

- **Artifact persistence** — `ArtifactDAL.create()` is ready; the call site in `artifact_store.py` is not yet wired. Easy addition when needed.
- **Replan tracking** — `plan_index` is always 0 for now (replan count tracking requires a counter on the Plan schema object, which is a future addition).
