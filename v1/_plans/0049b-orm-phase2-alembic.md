# 0049b — ORM/DAL Phase 2: Alembic Setup

**Status:** Implemented
**Phase:** 2 of 7

## What was built

### New files

| File | Purpose |
|---|---|
| `src/alembic.ini` | Alembic configuration — script_location, logging |
| `src/alembic/env.py` | Migration environment — URL conversion, metadata, offline/online runners |
| `src/alembic/script.py.mako` | Migration template with forward-only downgrade stub |
| `src/alembic/sql_runner.py` | `run_sql()`, `run_sql_dir()`, `list_sql_files()` helpers |
| `src/alembic/sql/functions/.gitkeep` | Placeholder — future DB functions (CREATE OR REPLACE) |
| `src/alembic/sql/triggers/.gitkeep` | Placeholder — future triggers (DROP IF EXISTS + CREATE) |
| `src/alembic/sql/schema/.gitkeep` | Placeholder — future structural SQL migrations |
| `src/alembic/sql/seeds/.gitkeep` | Placeholder — future seed data (ON CONFLICT DO UPDATE) |
| `src/alembic/versions/.gitkeep` | Placeholder — migration version files |

## Key design decisions

### URL conversion (async → sync)
Alembic doesn't support async drivers. `env.py` converts at migration time:
```
sqlite+aiosqlite:// → sqlite://
postgresql+asyncpg:// → postgresql+psycopg2://
```
Runtime code always uses the async driver. Only `env.py` uses sync.

### render_as_batch=True
Required for SQLite `ALTER TABLE` support (SQLite doesn't support column drops/renames natively). This is a no-op on Postgres. It means all `ALTER TABLE` statements in migrations use Alembic's batch mode regardless of backend.

### Briefbot exclusion
`env.py` imports `db.models` (owned models only) to register them with `SQLModel.metadata`. Briefbot models are intentionally excluded — they'll use a separate `MetaData()` instance (Phase 4).

### Forward-only migrations
`downgrade()` is always `pass`. On dev resets, archive the versions folder and create a new frozen baseline (same pattern as ed-platform's `archived_versions_dev_reset_20260321/`).

### sql_runner.py pattern
Matches ed-platform exactly:
- `run_sql(relative_path)` — single file
- `run_sql_dir(relative_dir, filenames)` — ordered batch
- `list_sql_files(relative_dir)` — sorted discovery (returns `[]` if dir is empty)

## Running migrations

```bash
# From repo root, with venv active:
cd src && alembic -c alembic.ini upgrade head

# Or from src/ directory:
alembic upgrade head
```

The `data/` directory (for SQLite) must exist — create it if running fresh:
```bash
mkdir -p data
```
