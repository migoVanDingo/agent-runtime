"""SQL file runner for Alembic migrations.

Mirrors the pattern from ed-platform/ed-database-management.
All SQL files must be idempotent:
  - Functions:  CREATE OR REPLACE FUNCTION ...
  - Triggers:   DROP TRIGGER IF EXISTS ...; CREATE TRIGGER ...
  - Schema:     CREATE TABLE IF NOT EXISTS ..., CREATE INDEX IF NOT EXISTS ...
  - Seeds:      INSERT ... ON CONFLICT (col) DO UPDATE SET ...

Usage inside a migration version file:
    from alembic_dir.sql_runner import run_sql, run_sql_dir, list_sql_files

    def upgrade() -> None:
        run_sql_dir("functions", list_sql_files("functions"))
        run_sql_dir("triggers", list_sql_files("triggers"))
        run_sql("seeds/001_initial_data.sql")
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from alembic import op

SQL_ROOT = Path(__file__).resolve().parent / "sql"


def run_sql(relative_path: str) -> None:
    """Read and execute a single SQL file relative to sql/."""
    sql = (SQL_ROOT / relative_path).read_text(encoding="utf-8")
    if sql.strip():
        op.execute(sql)


def run_sql_dir(relative_dir: str, filenames: Iterable[str]) -> None:
    """Execute all listed SQL files from a subdirectory, in order."""
    for fname in filenames:
        run_sql(f"{relative_dir}/{fname}")


def list_sql_files(relative_dir: str) -> list[str]:
    """Return sorted list of .sql filenames in a subdirectory."""
    d = SQL_ROOT / relative_dir
    if not d.exists():
        return []
    return sorted(p.name for p in d.glob("*.sql"))
