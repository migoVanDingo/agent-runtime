"""Async SQLAlchemy engine singletons.

Two named engines:
  agent_engine    — agent-runtime's own database (owned, migrations run here)
  briefbot_engine — external Briefbot SQLite (read-only, no migrations)

Switching from SQLite to Postgres requires only a settings change:
  AGENT_DB_URL=postgresql+asyncpg://user:pass@host/dbname

The connect_args for SQLite are passed via a dialect-aware helper so they
are silently ignored when the driver is not SQLite.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app_config import settings

_agent_engine: AsyncEngine | None = None
_briefbot_engine: AsyncEngine | None = None


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


async def get_agent_engine() -> AsyncEngine:
    global _agent_engine
    if _agent_engine is None:
        url = settings.agent_db_url
        kwargs: dict = {"echo": False, "future": True}
        if _is_sqlite(url):
            kwargs["connect_args"] = {"check_same_thread": False}
        _agent_engine = create_async_engine(url, **kwargs)
    return _agent_engine


async def get_briefbot_engine() -> AsyncEngine:
    global _briefbot_engine
    if _briefbot_engine is None:
        if not settings.briefbot_db_path:
            raise RuntimeError(
                "BRIEFBOT_DB_PATH is not configured. "
                "Add BRIEFBOT_DB_PATH=/path/to/briefbot.db to your .env file."
            )
        # Read-only URI — aiosqlite honours the ?mode=ro query param
        url = (
            f"sqlite+aiosqlite:///file:{settings.briefbot_db_path}"
            "?mode=ro&uri=true"
        )
        _briefbot_engine = create_async_engine(url, echo=False, future=True)
    return _briefbot_engine


async def dispose_agent_engine() -> None:
    global _agent_engine
    if _agent_engine is not None:
        await _agent_engine.dispose()
        _agent_engine = None


async def dispose_briefbot_engine() -> None:
    global _briefbot_engine
    if _briefbot_engine is not None:
        await _briefbot_engine.dispose()
        _briefbot_engine = None
