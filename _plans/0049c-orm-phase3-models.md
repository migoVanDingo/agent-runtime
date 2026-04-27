# 0049c — ORM/DAL Phase 3: Agent-Runtime Models

**Status:** Implemented
**Phase:** 3 of 7

## What was built

### New model files

| File | Table | Purpose |
|---|---|---|
| `src/db/models/agent_session.py` | `agent_session` | One row per user query |
| `src/db/models/plan.py` | `plan`, `step` | Planner output + execution steps |
| `src/db/models/artifact.py` | `artifact` | Artifact store registrations |

### Migration

`src/alembic/versions/0001_base.py` — frozen baseline, creates all 4 tables + 6 indexes.

Verified with `alembic upgrade head` — all tables present in `src/data/agent.db`.

## Schema summary

### agent_session
Primary key: `SESS{ULID}` · Tracks query, model, provider, status, timing, error

### plan
Primary key: `PLAN{ULID}` · FK → agent_session · Stores full steps_json from planner, plan_index for replans

### step
Primary key: `STEP{ULID}` · FK → plan, agent_session · action_type, tool, status, result (1000 char cap), retry_count, importance_score, duration_ms

### artifact
Primary key: `ARTI{ULID}` · FK → agent_session · key, tier (hot/warm/cold), content_preview (500 char cap), storage_path

## Notes

- Relationships (`Relationship()`) were omitted from models — SQLModel 0.0.21 + SQLAlchemy 2.x requires `Mapped[...]` annotations for list relationships, which adds boilerplate. DALs use explicit `select()` queries instead.
- `render_as_batch=True` in `env.py` enables SQLite `ALTER TABLE` support via Alembic batch mode (no-op on Postgres).
- `data/` directory must exist before running migrations. Default path: `src/data/agent.db` (relative to CWD when running `alembic` from `src/`).
- Writes are gated behind `ENABLE_SESSION_PERSISTENCE=false` — these tables exist but are not written to until the flag is enabled (Phase 7).

## Running migrations

```bash
# From repo root with venv active:
mkdir -p src/data
cd src && alembic -c alembic.ini upgrade head
```
