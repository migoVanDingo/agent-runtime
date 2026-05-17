# 0049a — ORM/DAL Phase 1: Foundation

**Status:** Implemented
**Phase:** 1 of 7

## What was built

### New dependencies (`requirements.txt`)
- `sqlmodel>=0.0.21`
- `sqlalchemy[asyncio]>=2.0`
- `aiosqlite>=0.20.0`
- `alembic>=1.13`

Note: `ulid-py` was already present — used `ulid.new().str` API (no new dep needed).

### New settings fields (`src/settings.py`)
```python
agent_db_url: str           # default: sqlite+aiosqlite:///./data/agent.db
briefbot_db_path: Optional[str]   # BRIEFBOT_DB_PATH env var
enable_session_persistence: bool  # default: False
```

### New files

| File | Purpose |
|---|---|
| `src/db/__init__.py` | Package marker |
| `src/db/utils/__init__.py` | Package marker |
| `src/db/utils/id_prefix.py` | `IdPrefix` enum — SESS, PLAN, STEP, ARTI |
| `src/db/utils/generate_id.py` | `generate_id(prefix)` → prefixed ULID string |
| `src/db/base.py` | `BaseModel` — created_at, updated_at, deleted_at, is_active |
| `src/db/engine.py` | `get_agent_engine()`, `get_briefbot_engine()`, dispose helpers |
| `src/db/session.py` | `agent_session()`, `briefbot_session()` async context managers |
| `src/db/dal/__init__.py` | Package marker |
| `src/db/dal/briefbot/__init__.py` | Package marker |
| `src/db/models/__init__.py` | Re-exports all owned models (used by alembic env.py) |
| `src/db/models/briefbot/__init__.py` | Package marker |

## ID format

`{PREFIX}{ULID}` — 30 characters total, time-ordered, URL-safe.

```
SESS01KQ5VXTG800BCB692R1B6XEP4   ← AgentSession
PLAN01KQ5VXTG800BCB692R1B6XEP4   ← Plan
STEP01KQ5VXTG800BCB692R1B6XEP4   ← Step
ARTI01KQ5VXTG800BCB692R1B6XEP4   ← Artifact
```

## Notes

- `StrEnum` is Python 3.11+; using `class IdPrefix(str, Enum)` for 3.10 compat.
- `get_briefbot_engine()` raises `RuntimeError` immediately if `BRIEFBOT_DB_PATH` is unset — tools catch this and return a user-friendly message.
- Briefbot engine uses `?mode=ro&uri=true` to prevent any accidental writes.
- `connect_args={"check_same_thread": False}` applied only for SQLite URLs — omitted for Postgres.
- No existing code was modified except `settings.py` (additive) and `requirements.txt` (additive).
