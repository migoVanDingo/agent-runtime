# 0049 — Async ORM / DAL Layer with Alembic

**Status:** Design Review
**Scope:** Introduce SQLModel + SQLAlchemy async ORM, a typed DAL layer, and Alembic migrations into agent-runtime. Provide read-only access to the external Briefbot SQLite database. Stub agent-runtime's own persistence models (session, plan, step, artifact) behind a feature flag — schema is live, writes are toggled off until ready.

---

## Motivation

1. **Briefbot integration** — The nightly pipeline collects and scores research papers, blogs, and news from dozens of sources. The agent should query this corpus instead of (or before) hitting the live web.
2. **Future persistence** — Session history, plans, step results, and artifacts are currently ephemeral. The infrastructure should be ready to persist them for analytics, training data, or contextual recall — without forcing that decision now.
3. **Database portability** — Starting on SQLite; must be droppable onto Postgres (or anything else) with only a settings change. No SQLite-specific SQL in application code.
4. **FastAPI readiness** — The runtime may be embedded in FastAPI services. Async SQLAlchemy is the right foundation.

---

## Design Principles

- **Async-first:** `AsyncEngine` + `AsyncSession` throughout. Sync only at Alembic migration time (Alembic limitation).
- **SQLModel for models:** Single class serves as both ORM table and Pydantic schema.
- **Typed DAL:** `BaseDAL[T]` generic base. One specialized DAL class per table.
- **Two named databases:** `agent_db` (owned, migrations here), `briefbot_db` (external, read-only, no migrations).
- **Idempotent SQL:** All SQL files use `CREATE OR REPLACE`, `DROP ... IF EXISTS`, `IF NOT EXISTS`, `ON CONFLICT DO UPDATE`.
- **Feature-flagged persistence:** Agent-runtime's own tables exist in the schema from day one. Writes are gated behind `ENABLE_SESSION_PERSISTENCE=false`. Flip the flag, nothing else changes.
- **ULID + prefix IDs:** Time-ordered, human-readable, no collisions. Format: `{PREFIX}{ULID}` — e.g. `ARTI01ARZ3NDEKTSV4RRFFQ69G5FAV`.

---

## ID Prefix Registry

| Entity | Prefix | Example |
|---|---|---|
| AgentSession | `SESS` | `SESS01ARZ3NDEKTSV4RRFFQ69G5FAV` |
| Plan | `PLAN` | `PLAN01ARZ3NDEKTSV4RRFFQ69G5FAV` |
| Step | `STEP` | `STEP01ARZ3NDEKTSV4RRFFQ69G5FAV` |
| Artifact | `ARTI` | `ARTI01ARZ3NDEKTSV4RRFFQ69G5FAV` |

---

## Directory Layout (final state after all phases)

```
src/
  db/
    __init__.py
    engine.py                  # get_agent_engine(), get_briefbot_engine()
    session.py                 # agent_session(), briefbot_session() context managers
    base.py                    # BaseModel — id, timestamps, soft delete
    utils/
      __init__.py
      id_prefix.py             # IdPrefix enum
      generate_id.py           # generate_id(prefix) → ULID string
    models/
      __init__.py              # re-exports all models
      # === AGENT-RUNTIME OWNED ===
      agent_session.py         # AgentSession
      plan.py                  # Plan, Step
      artifact.py              # Artifact
      # === BRIEFBOT READ-ONLY MIRRORS ===
      briefbot/
        __init__.py
        item.py                # BriefbotItem → maps to `items`
        cluster.py             # BriefbotCluster → maps to `clusters`
                               # BriefbotClusterMembership → maps to `cluster_memberships`
        topic.py               # BriefbotTopic → maps to `topic_profiles`
    dal/
      __init__.py
      base_dal.py              # BaseDAL[T]
      # === AGENT-RUNTIME OWNED ===
      agent_session_dal.py
      plan_dal.py
      step_dal.py
      artifact_dal.py
      # === BRIEFBOT READ-ONLY ===
      briefbot/
        __init__.py
        items_dal.py
        clusters_dal.py
        topics_dal.py

  alembic/
    env.py                     # async→sync URL conversion, SQLModel.metadata
    sql_runner.py              # run_sql(), run_sql_dir()
    sql/
      functions/               # 001_*.sql, CREATE OR REPLACE FUNCTION
      triggers/                # 001_*.sql, DROP TRIGGER IF EXISTS + CREATE
      schema/                  # 001_*.sql, structural changes
      seeds/                   # 001_*.sql, ON CONFLICT DO UPDATE
    versions/
      0001_base.py             # all agent-runtime owned tables
  alembic.ini
```

---

## Phase 1 — Foundation

**Goal:** Engine, session, base model, ID generation. No tables yet. Nothing breaks in existing code.

### 1.1 New dependencies (`requirements.txt`)

```
sqlmodel>=0.0.21
sqlalchemy[asyncio]>=2.0
aiosqlite>=0.20.0
python-ulid>=2.0
alembic>=1.13
```

### 1.2 New settings fields (`src/settings.py`)

```python
# === DATABASE ===
agent_db_url: str = Field(
    default="sqlite+aiosqlite:///./data/agent.db",
    validation_alias=env_alias("AGENT_DB_URL", "agent_db_url"),
)

briefbot_db_path: Optional[str] = Field(
    default=None,
    validation_alias=env_alias("BRIEFBOT_DB_PATH", "briefbot_db_path"),
)

enable_session_persistence: bool = Field(
    default=False,
    validation_alias=env_alias("ENABLE_SESSION_PERSISTENCE", "enable_session_persistence"),
)
```

### 1.3 `src/db/utils/id_prefix.py`

```python
from enum import StrEnum

class IdPrefix(StrEnum):
    SESSION  = "SESS"
    PLAN     = "PLAN"
    STEP     = "STEP"
    ARTIFACT = "ARTI"
```

### 1.4 `src/db/utils/generate_id.py`

```python
from ulid import ULID
from db.utils.id_prefix import IdPrefix

def generate_id(prefix: IdPrefix) -> str:
    return f"{prefix}{ULID()}"
```

### 1.5 `src/db/base.py`

```python
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class BaseModel(SQLModel):
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
    deleted_at: Optional[datetime] = Field(default=None, nullable=True)
    is_active: bool = Field(default=True, nullable=False)
```

### 1.6 `src/db/engine.py`

```python
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from app_config import settings

_agent_engine: AsyncEngine | None = None
_briefbot_engine: AsyncEngine | None = None

async def get_agent_engine() -> AsyncEngine:
    global _agent_engine
    if _agent_engine is None:
        _agent_engine = create_async_engine(
            settings.agent_db_url,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},  # SQLite only
        )
    return _agent_engine

async def get_briefbot_engine() -> AsyncEngine:
    global _briefbot_engine
    if _briefbot_engine is None:
        if not settings.briefbot_db_path:
            raise RuntimeError("BRIEFBOT_DB_PATH is not configured")
        url = f"sqlite+aiosqlite:///file:{settings.briefbot_db_path}?mode=ro&uri=true"
        _briefbot_engine = create_async_engine(url, echo=False, future=True)
    return _briefbot_engine

async def dispose_agent_engine() -> None:
    global _agent_engine
    if _agent_engine:
        await _agent_engine.dispose()
        _agent_engine = None

async def dispose_briefbot_engine() -> None:
    global _briefbot_engine
    if _briefbot_engine:
        await _briefbot_engine.dispose()
        _briefbot_engine = None
```

Note: `connect_args={"check_same_thread": False}` is stripped automatically when switching to Postgres — it's only accepted by the SQLite driver.

### 1.7 `src/db/session.py`

```python
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from db.engine import get_agent_engine, get_briefbot_engine

async def _make_factory(engine_fn):
    engine = await engine_fn()
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@asynccontextmanager
async def agent_session():
    factory = await _make_factory(get_agent_engine)
    async with factory() as session:
        yield session

@asynccontextmanager
async def briefbot_session():
    factory = await _make_factory(get_briefbot_engine)
    async with factory() as session:
        yield session
```

---

## Phase 2 — Alembic Setup

**Goal:** Migrations wired up, `alembic upgrade head` creates `data/agent.db`. Briefbot is explicitly out of scope with a comment in env.py.

### 2.1 `src/alembic.ini`

```ini
[alembic]
script_location = %(here)s/alembic
prepend_sys_path = %(here)s
sqlalchemy.url = driver_not_used

[loggers]
keys = root,sqlalchemy,alembic

[logger_alembic]
level = INFO
```

### 2.2 `src/alembic/env.py`

Mirrors ed-platform pattern:
- Imports `SQLModel.metadata` as `target_metadata`
- Converts `sqlite+aiosqlite://` → `sqlite://` for sync migration connection
- Converts `postgresql+asyncpg://` → `postgresql+psycopg2://` when on Postgres
- Adds a guard comment: `# Briefbot DB is external/read-only — never run migrations against it`
- Imports all models from `db.models` to register them with metadata before Alembic runs

### 2.3 `src/alembic/sql_runner.py`

Direct port of ed-platform's `sql_runner.py`:

```python
from pathlib import Path
from typing import Iterable
from alembic import op

SQL_ROOT = Path(__file__).resolve().parent / "sql"

def run_sql(relative_path: str) -> None:
    sql = (SQL_ROOT / relative_path).read_text()
    op.execute(sql)

def run_sql_dir(relative_dir: str, filenames: Iterable[str]) -> None:
    for fname in filenames:
        run_sql(f"{relative_dir}/{fname}")

def list_sql_files(relative_dir: str) -> list[str]:
    return sorted(p.name for p in (SQL_ROOT / relative_dir).glob("*.sql"))
```

### 2.4 `src/alembic/sql/` subdirs

Create empty directories with `.gitkeep` files:
- `functions/` — placeholder for future DB functions
- `triggers/` — placeholder for future triggers
- `schema/` — placeholder for structural migrations
- `seeds/` — placeholder for seed data

### 2.5 `src/alembic/versions/0001_base.py`

Contains the DDL for all four agent-runtime owned tables (defined in Phase 3). Structure follows ed-platform's frozen baseline pattern:

```python
"""Base schema for agent-runtime

Revision ID: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DDL_STATEMENTS = (
    # ... CREATE TABLE statements ...
)

def upgrade() -> None:
    for stmt in DDL_STATEMENTS:
        op.execute(stmt)
    # No functions/triggers/seeds yet — sql/ dirs are scaffolded for future use

def downgrade() -> None:
    pass  # Frozen baseline — no downgrade
```

---

## Phase 3 — Agent-Runtime Models

**Goal:** Define `AgentSession`, `Plan`, `Step`, `Artifact` as full `table=True` SQLModel classes. These tables are created by the migration but writes are gated by the feature flag.

### 3.1 `src/db/models/agent_session.py`

```python
class AgentSession(BaseModel, table=True):
    __tablename__ = "agent_session"

    id: str = Field(primary_key=True, default_factory=lambda: generate_id(IdPrefix.SESSION))
    original_query: str
    model: str
    provider: str
    status: str = "active"          # active | completed | error
    total_steps: int = 0
    total_tokens: int | None = None
    error: str | None = None
    completed_at: datetime | None = None

    plans: list["Plan"] = Relationship(back_populates="session")
```

### 3.2 `src/db/models/plan.py`

```python
class Plan(BaseModel, table=True):
    __tablename__ = "plan"

    id: str = Field(primary_key=True, default_factory=lambda: generate_id(IdPrefix.PLAN))
    session_id: str = Field(foreign_key="agent_session.id")
    plan_index: int = 0             # 0 = original, 1+ = replan
    original_query: str
    steps_json: str                 # JSON serialized step list from planner
    replan_reason: str | None = None

    session: AgentSession = Relationship(back_populates="plans")
    steps: list["Step"] = Relationship(back_populates="plan")

class Step(BaseModel, table=True):
    __tablename__ = "step"

    id: str = Field(primary_key=True, default_factory=lambda: generate_id(IdPrefix.STEP))
    plan_id: str = Field(foreign_key="plan.id")
    session_id: str = Field(foreign_key="agent_session.id")
    step_index: int
    action_type: str
    tool: str | None = None
    description: str
    status: str                     # pending | success | error | skipped
    result: str | None = None       # first 1000 chars
    error: str | None = None
    retry_count: int = 0
    importance_score: float | None = None
    duration_ms: int | None = None

    plan: Plan = Relationship(back_populates="steps")
```

### 3.3 `src/db/models/artifact.py`

```python
class Artifact(BaseModel, table=True):
    __tablename__ = "artifact"

    id: str = Field(primary_key=True, default_factory=lambda: generate_id(IdPrefix.ARTIFACT))
    session_id: str = Field(foreign_key="agent_session.id")
    key: str                        # the artifact store key (e.g. "search_results")
    mime_type: str | None = None
    size_bytes: int | None = None
    tier: str                       # hot | warm | cold
    content_preview: str | None = None  # first 500 chars
    storage_path: str | None = None  # for cold tier
```

### 3.4 `src/db/models/__init__.py`

Re-exports all models. This file is imported by `alembic/env.py` to register all tables with `SQLModel.metadata` before migration runs.

---

## Phase 4 — Briefbot Read-Only Models

**Goal:** Type-safe mirrors of the Briefbot tables. No `alembic` involvement — these tables are owned by Briefbot.

### 4.1 `src/db/models/briefbot/item.py`

Maps to Briefbot's `items` table. Key columns for agent use:

```python
class BriefbotItem(SQLModel, table=True):
    __tablename__ = "items"
    __table_args__ = {"schema": None, "info": {"bind_key": "briefbot"}}

    item_id: str = Field(primary_key=True)
    title: str | None = None
    summary: str | None = None
    canonical_url: str | None = None
    url: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    source_category: str | None = None
    source_tier: int | None = None
    published_at: str | None = None     # ISO string (Briefbot stores as text)
    fetched_at: str | None = None
    score: float | None = None
    score_opportunity: float | None = None
    opportunity_reason: str | None = None
    tags_json: str | None = None         # JSON array stored as TEXT
    watch_hits_json: str | None = None
```

### 4.2 `src/db/models/briefbot/cluster.py`

Maps to `clusters` and `cluster_memberships`.

### 4.3 `src/db/models/briefbot/topic.py`

Maps to `topic_profiles`.

**Important note:** Briefbot models use `table=True` but are excluded from `SQLModel.metadata` for migration purposes. This is achieved by registering them against a separate `MetaData()` instance — Alembic's `env.py` only references the agent-runtime metadata.

---

## Phase 5 — DAL Layer

**Goal:** `BaseDAL[T]` and all specialized DALs. Tool implementations will call these instead of raw SQL.

### 5.1 `src/db/dal/base_dal.py`

```python
from typing import TypeVar, Generic, Type, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, select

T = TypeVar("T", bound=SQLModel)

class BaseDAL(Generic[T]):
    def __init__(self, model: Type[T], session: AsyncSession):
        self.model = model
        self.session = session

    async def get_by_id(self, id: str) -> Optional[T]:
        stmt = select(self.model).where(
            self.model.id == id,
            self.model.is_active == True,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(self, obj: T) -> T:
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def soft_delete(self, obj: T) -> None:
        obj.deleted_at = utcnow()
        obj.is_active = False
        await self.save(obj)
```

### 5.2 Agent-runtime DALs

Each follows the specialized pattern from ed-platform:

- `AgentSessionDAL(BaseDAL[AgentSession])` — create, get_by_id, mark_completed, mark_error
- `PlanDAL(BaseDAL[Plan])` — create, list_by_session
- `StepDAL(BaseDAL[Step])` — create, list_by_plan, update_status
- `ArtifactDAL(BaseDAL[Artifact])` — create, list_by_session

All writes gated by a guard at the call site:
```python
if settings.enable_session_persistence:
    async with agent_session() as session:
        dal = AgentSessionDAL(session)
        await dal.create(...)
```

### 5.3 Briefbot DALs (read-only)

**`ItemsDAL`:**

```python
class ItemsDAL:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def search(
        self,
        query: str,
        *,
        days: int = 30,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
        order_by: str = "score",        # "score" | "date"
    ) -> list[BriefbotItem]: ...

    async def get_by_id(self, item_id: str) -> BriefbotItem | None: ...
```

Search strategy: `title LIKE %query%` OR `summary LIKE %query%`, filtered by `fetched_at >= now - days`, optionally by `source_category`, tags via `json_each(tags_json)`. Ordered by `score DESC` or `published_at DESC`.

**`ClustersDAL`:**

```python
async def get_trending(
    self,
    *,
    window: str = "3d",    # "1d" | "3d" | "7d"
    limit: int = 10,
) -> list[BriefbotCluster]: ...

async def get_items_for_cluster(
    self,
    cluster_id: int,
    limit: int = 10,
) -> list[BriefbotItem]: ...
```

**`TopicsDAL`:**

```python
async def get_top_topics(
    self,
    *,
    limit: int = 20,
    min_momentum: float = 0.0,
) -> list[BriefbotTopic]: ...
```

---

## Phase 6 — Briefbot Tools (wired to DAL)

**Goal:** Three new tools in a `briefbot` toolset that call the DAL. These replace the earlier plan to hit Briefbot's HTTP API or raw sqlite3.

| Tool | DAL | Description |
|---|---|---|
| `briefbot_search` | `ItemsDAL.search()` | Search indexed items by topic, date range, category, tags |
| `briefbot_trending` | `ClustersDAL.get_trending()` + `TopicsDAL.get_top_topics()` | What's trending right now (velocity + topic momentum) |
| `briefbot_item` | `ItemsDAL.get_by_id()` | Full detail on a specific item |

Added to `toolsets.py` as `BRIEFBOT` toolset with routing rules:
- "what's been written about X" → `briefbot_search`
- "what's trending / hot / new in AI" → `briefbot_trending`
- Preference over `web_search` for research/paper queries (corpus is scored + deduped)

Added to `ActionType` enum: `BRIEFBOT = "briefbot"`
Added to `config.yml` toolset_descriptions.
Added to `src/alembic.ini`: briefbot db is external, never run migrations against it.

---

## Phase 7 — Runtime Wiring (feature-flagged)

**Goal:** Optionally persist session data through the pipeline. All writes behind `ENABLE_SESSION_PERSISTENCE`.

Touch points in the runtime:

| Location | Change |
|---|---|
| `runtime/agent.py` or session init | Create `AgentSession` row, store ID in context |
| `runtime/stages/planning.py` | Create `Plan` row after each planner call |
| `runtime/stages/execution.py` | Create `Step` row after each step; update status/result |
| `runtime/artifact_store.py` | Create `Artifact` row when item registered |
| Session cleanup | Mark `AgentSession.status = completed` or `error` |

None of this is implemented until `ENABLE_SESSION_PERSISTENCE=true`. When false, the runtime behaves identically to today.

---

## Migration Conventions

- Files: `{NNNN}_{short_description}.py` — e.g. `0001_base.py`, `0002_add_step_token_count.py`
- SQL functions: `src/alembic/sql/functions/NNN_name.sql` — `CREATE OR REPLACE FUNCTION`
- SQL triggers: `src/alembic/sql/triggers/NNN_name.sql` — `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER`
- SQL seeds: `src/alembic/sql/seeds/NNN_name.sql` — `INSERT ... ON CONFLICT DO UPDATE`
- All migrations are forward-only (no downgrade). On dev reset: archive to `archived_versions_dev_reset_YYYYMMDD/` and create a new consolidated baseline.
- Briefbot schema changes: update models manually, write a note migration documenting the change.

---

## Briefbot Model Maintenance

When Briefbot's schema changes:
1. Update the model file in `src/db/models/briefbot/`
2. Write `NNNN_briefbot_schema_update_note.py` — a no-op migration that documents the change and the Briefbot version/date
3. No DDL runs against Briefbot's DB — the migration is purely a record

---

## Settings Summary (`.env` additions)

```bash
# Agent-runtime database (SQLite default, swap URL for Postgres)
AGENT_DB_URL=sqlite+aiosqlite:///./data/agent.db

# External Briefbot database (read-only)
BRIEFBOT_DB_PATH=/Users/bubz/Developer/agent/projects/ai-assistant/data/briefbot.db

# Flip to true when ready to persist session/plan/step/artifact data
ENABLE_SESSION_PERSISTENCE=false
```

---

## Implementation Order

| Phase | Deliverable | Risk |
|---|---|---|
| 1 | Foundation (engine, session, base, IDs) | Low — no existing code changes |
| 2 | Alembic setup + sql_runner | Low — scaffolding only |
| 3 | Agent-runtime models + 0001_base migration | Low — behind feature flag |
| 4 | Briefbot read-only models | Low — separate metadata |
| 5 | DAL layer | Low — new code only |
| 6 | Briefbot toolset (3 tools) | Medium — new toolset wired to runtime |
| 7 | Runtime wiring (feature-flagged persistence) | Medium — touches execution pipeline |

Phases 1–5 are pure infrastructure — zero impact on the running agent. Phase 6 adds user-facing capability. Phase 7 is opt-in.
