"""Alembic environment — agent-runtime database only.

IMPORTANT: This file manages migrations for agent-runtime's OWN database
(agent_db). The Briefbot database is external and read-only — never run
migrations or schema changes against it from here.

URL conversion:
  sqlite+aiosqlite://  →  sqlite://          (sync driver for Alembic)
  postgresql+asyncpg:// →  postgresql+psycopg2://  (sync driver for Alembic)

Alembic does not support async drivers. The runtime uses async engines;
this file uses sync engines only for migration execution.
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool
from sqlmodel import SQLModel

# Ensure src/ is on the path so imports work when run via `alembic` CLI
_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app_config import settings  # noqa: E402 — must come after sys.path fix

# Register all owned models with SQLModel.metadata before Alembic runs.
import db.models  # noqa: F401, E402

# Also import briefbot models so SQLAlchemy knows about them (for query mapping),
# but we explicitly exclude their table names from Alembic's autogenerate scope.
import db.models.briefbot.item  # noqa: F401
import db.models.briefbot.cluster  # noqa: F401
import db.models.briefbot.topic  # noqa: F401

alembic_config = context.config

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

target_metadata = SQLModel.metadata

# Briefbot tables that live in an external DB and must never be migrated here.
_BRIEFBOT_TABLES = frozenset({
    "items",
    "clusters",
    "cluster_memberships",
    "topic_profiles",
    # Additional briefbot tables (not queried by agent, listed for safety)
    "cluster_events",
    "dashboard_queries",
    "dashboard_story_feedback",
    "dashboard_story_feedback_events",
    "dashboard_favorite_folders",
    "dashboard_favorite_links",
    "discovered_feeds",
    "feed_cache",
    "exec_summary_cache",
    "summaries",
})


def include_object(obj, name, type_, reflected, compare_to):
    """Exclude Briefbot tables from Alembic's migration scope."""
    if type_ == "table" and name in _BRIEFBOT_TABLES:
        return False
    return True


def _sync_url(async_url: str) -> str:
    """Convert an async driver URL to its sync equivalent for Alembic."""
    return (
        async_url
        .replace("sqlite+aiosqlite", "sqlite")
        .replace("postgresql+asyncpg", "postgresql+psycopg2")
    )


def run_migrations_offline() -> None:
    url = _sync_url(settings.agent_db_url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        include_object=include_object,
        compare_type=False,  # SQLite type names are unreliable for comparison
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _sync_url(settings.agent_db_url)
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            include_object=include_object,
            compare_type=False,  # SQLite type names are unreliable for comparison
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
