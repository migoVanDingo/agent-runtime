"""Async session context managers.

Usage:
    async with agent_session() as session:
        dal = AgentSessionDAL(session)
        await dal.get_by_id("SESS...")

    async with briefbot_session() as session:
        dal = ItemsDAL(session)
        results = await dal.search("transformer attention")

Sessions are expire_on_commit=False to avoid lazy-load errors after commit.
Each context manager yields a fresh AsyncSession from its own sessionmaker.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.engine import get_agent_engine, get_briefbot_engine


async def _make_factory(engine_coro) -> async_sessionmaker:
    engine = await engine_coro()
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def agent_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession connected to the agent-runtime database."""
    factory = await _make_factory(get_agent_engine)
    async with factory() as session:
        yield session


@asynccontextmanager
async def briefbot_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a read-only AsyncSession connected to the Briefbot database."""
    factory = await _make_factory(get_briefbot_engine)
    async with factory() as session:
        yield session
