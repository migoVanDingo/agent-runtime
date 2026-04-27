"""Base schema for agent-runtime owned tables.

Revision ID: 0001
Revises: None
Create Date: 2026-04-26

This is the frozen baseline migration. All four agent-runtime owned tables
are created here. Briefbot tables are external and never managed here.

Downgrade is intentionally a no-op — forward-only migrations.
On a dev reset, archive this file and create a new consolidated baseline.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Raw DDL for all owned tables + indexes.
# Dialect-neutral SQL — works for both SQLite and Postgres.
DDL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS agent_session (
        id          VARCHAR     NOT NULL,
        created_at  DATETIME    NOT NULL,
        updated_at  DATETIME    NOT NULL,
        deleted_at  DATETIME,
        is_active   BOOLEAN     NOT NULL DEFAULT 1,
        original_query VARCHAR  NOT NULL,
        model       VARCHAR     NOT NULL,
        provider    VARCHAR     NOT NULL,
        status      VARCHAR     NOT NULL DEFAULT 'active',
        total_steps INTEGER     NOT NULL DEFAULT 0,
        total_tokens INTEGER,
        error       VARCHAR,
        completed_at DATETIME,
        PRIMARY KEY (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plan (
        id              VARCHAR NOT NULL,
        created_at      DATETIME NOT NULL,
        updated_at      DATETIME NOT NULL,
        deleted_at      DATETIME,
        is_active       BOOLEAN NOT NULL DEFAULT 1,
        session_id      VARCHAR NOT NULL,
        plan_index      INTEGER NOT NULL DEFAULT 0,
        original_query  VARCHAR NOT NULL,
        steps_json      VARCHAR NOT NULL,
        replan_reason   VARCHAR,
        PRIMARY KEY (id),
        FOREIGN KEY (session_id) REFERENCES agent_session (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS step (
        id              VARCHAR NOT NULL,
        created_at      DATETIME NOT NULL,
        updated_at      DATETIME NOT NULL,
        deleted_at      DATETIME,
        is_active       BOOLEAN NOT NULL DEFAULT 1,
        plan_id         VARCHAR NOT NULL,
        session_id      VARCHAR NOT NULL,
        step_index      INTEGER NOT NULL,
        action_type     VARCHAR NOT NULL,
        tool            VARCHAR,
        description     VARCHAR NOT NULL,
        status          VARCHAR NOT NULL,
        result          VARCHAR,
        error           VARCHAR,
        retry_count     INTEGER NOT NULL DEFAULT 0,
        importance_score REAL,
        duration_ms     INTEGER,
        PRIMARY KEY (id),
        FOREIGN KEY (plan_id)    REFERENCES plan (id),
        FOREIGN KEY (session_id) REFERENCES agent_session (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifact (
        id              VARCHAR NOT NULL,
        created_at      DATETIME NOT NULL,
        updated_at      DATETIME NOT NULL,
        deleted_at      DATETIME,
        is_active       BOOLEAN NOT NULL DEFAULT 1,
        session_id      VARCHAR NOT NULL,
        key             VARCHAR NOT NULL,
        mime_type       VARCHAR,
        size_bytes      INTEGER,
        tier            VARCHAR NOT NULL,
        content_preview VARCHAR,
        storage_path    VARCHAR,
        PRIMARY KEY (id),
        FOREIGN KEY (session_id) REFERENCES agent_session (id)
    )
    """,
    # Indexes for common query patterns
    "CREATE INDEX IF NOT EXISTS ix_plan_session_id     ON plan     (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_step_plan_id        ON step     (plan_id)",
    "CREATE INDEX IF NOT EXISTS ix_step_session_id     ON step     (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_artifact_session_id ON artifact (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_agent_session_status ON agent_session (status)",
    "CREATE INDEX IF NOT EXISTS ix_agent_session_created_at ON agent_session (created_at)",
)


def upgrade() -> None:
    for stmt in DDL_STATEMENTS:
        op.execute(sa.text(stmt.strip()))

    # SQL functions, triggers, and seeds are scaffolded but empty for this baseline.
    # Add numbered .sql files to src/alembic/sql/{functions,triggers,seeds}/ as needed.
    # Example:
    #   from alembic.sql_runner import run_sql_dir, list_sql_files
    #   run_sql_dir("functions", list_sql_files("functions"))


def downgrade() -> None:
    pass  # Forward-only — no downgrade supported
