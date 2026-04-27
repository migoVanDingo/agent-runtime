"""Synchronous bridge for running async DAL code from sync tool execute() methods.

The tool layer is synchronous (BaseTool.execute → str). The DAL is async.
This module provides a simple way to call async DAL functions from sync tools
without changing the tool interface.

Usage:
    from db.sync import run_async
    results = run_async(some_async_function(arg1, arg2))

This creates a fresh event loop per call — appropriate for the agent's
sync tool execution model. If the runtime moves to async in the future,
tools can be updated to use `await` directly and this bridge can be removed.
"""
from __future__ import annotations

import asyncio
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine synchronously.

    Creates a new event loop, runs the coroutine to completion, and closes
    the loop. Safe to call from any sync context (including threads).
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
