"""Bounded async queue with selective drop policy.

On overflow, only TokenChunk events are dropped (oldest first). Lifecycle
events (session/turn/stage/tool) are never dropped — the UI's state machine
depends on them arriving exactly once.

All access to the internal buffer happens on the asyncio event loop (cooperative
multitasking), so no locking is needed for the buffer itself.
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import AsyncIterator

from service.events import AgentEvent, TokenChunk


class BoundedDropQueue:
    """Async queue that protects lifecycle events from overflow drops.

    drop_count is incremented each time a TokenChunk is discarded. The TUI
    reads this to show a throttled indicator in the status bar.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        self._maxsize = maxsize
        self._buffer: deque[AgentEvent] = deque()
        self._ready: asyncio.Event = asyncio.Event()
        self._closed = False
        self.drop_count = 0

    async def put(self, event: AgentEvent) -> None:
        """Enqueue an event. Never blocks; may drop oldest TokenChunk on overflow."""
        if self._closed:
            return
        if len(self._buffer) >= self._maxsize:
            # Find and drop the oldest TokenChunk to make room.
            for i, item in enumerate(self._buffer):
                if isinstance(item, TokenChunk):
                    del self._buffer[i]
                    self.drop_count += 1
                    break
            # If no TokenChunk found the buffer is full of lifecycle events.
            # Enqueue anyway — in practice this cannot happen at normal throughput.
        self._buffer.append(event)
        self._ready.set()

    async def __aiter__(self) -> AsyncIterator[AgentEvent]:
        """Yield events indefinitely until close() is called."""
        while True:
            if not self._buffer:
                if self._closed:
                    return
                # Clear the ready flag, then re-check before waiting.
                # Between clear() and wait(), no other coroutine runs (cooperative),
                # so this is race-condition-free.
                self._ready.clear()
                if not self._buffer and not self._closed:
                    await self._ready.wait()
                if not self._buffer and self._closed:
                    return
                continue
            yield self._buffer.popleft()

    async def close(self) -> None:
        """Signal end-of-stream to all consumers."""
        self._closed = True
        self._ready.set()
