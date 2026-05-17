"""Integration tests for InProcessAgentService.

These tests require a real agent and validate the event sequence
produced by the service layer. Run with:
    pytest tests/integration/test_service.py -v

Tests are skipped if ANTHROPIC_API_KEY is not set, so CI stays green.
"""
from __future__ import annotations

import asyncio
import os
import pytest

from service.events import (
    TurnStarted, TurnCompleted, TurnFailed, TurnCancelled,
    MessageComplete, TokenChunk, StageStarted,
)
from service.queue import BoundedDropQueue


# ── Unit tests (no API key needed) ───────────────────────────────────────────

class TestBoundedDropQueue:
    """Unit tests for BoundedDropQueue."""

    @pytest.mark.asyncio
    async def test_basic_put_and_get(self):
        q = BoundedDropQueue(maxsize=10)
        event = TurnStarted(session_id="s1", turn_id="t1")
        await q.put(event)
        await q.close()

        received = []
        async for e in q:
            received.append(e)
        assert len(received) == 1
        assert received[0].type == "turn.started"

    @pytest.mark.asyncio
    async def test_drops_token_chunks_on_overflow(self):
        q = BoundedDropQueue(maxsize=3)
        # Fill with 3 TokenChunks.
        for i in range(3):
            await q.put(TokenChunk(session_id="s1", turn_id="t1", text=f"chunk{i}"))

        assert q.drop_count == 0

        # Adding a 4th should drop the oldest TokenChunk.
        await q.put(TokenChunk(session_id="s1", turn_id="t1", text="chunk4"))
        assert q.drop_count == 1
        assert q._buffer[0].text == "chunk1"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_never_drops_lifecycle_events(self):
        q = BoundedDropQueue(maxsize=2)
        # Fill with lifecycle events (not TokenChunks).
        await q.put(TurnStarted(session_id="s1", turn_id="t1"))
        await q.put(TurnCompleted(session_id="s1", turn_id="t1"))

        # Adding a TokenChunk now — no TokenChunk to drop, so it's enqueued.
        await q.put(TokenChunk(session_id="s1", turn_id="t1", text="hi"))
        assert q.drop_count == 0  # no drops occurred
        assert len(q._buffer) == 3  # all three enqueued

    @pytest.mark.asyncio
    async def test_close_terminates_iterator(self):
        q = BoundedDropQueue()
        await q.put(TurnStarted(session_id="s1"))
        await q.close()

        count = 0
        async for _ in q:
            count += 1
        assert count == 1  # exactly the one event before close


# ── Service construction tests (no API key needed) ────────────────────────────

class TestInProcessAgentServiceConstruction:

    def test_service_can_be_imported(self):
        from service.inprocess import InProcessAgentService, NoopSpinner, TUIUserGate
        assert InProcessAgentService is not None
        assert NoopSpinner is not None
        assert TUIUserGate is not None

    @pytest.mark.asyncio
    async def test_service_constructs_and_closes(self):
        from agent import Agent
        from service.inprocess import InProcessAgentService

        agent = Agent(verbose=False)
        svc = InProcessAgentService(agent, session_id="test-construction")
        assert svc.session_id == "test-construction"
        assert not svc.is_busy
        await svc.close()

    @pytest.mark.asyncio
    async def test_noop_spinner_replaced(self):
        from agent import Agent
        from service.inprocess import InProcessAgentService, NoopSpinner

        agent = Agent(verbose=False)
        svc = InProcessAgentService(agent, session_id="test-spinner")
        assert isinstance(agent.spinner, NoopSpinner)
        await svc.close()

    @pytest.mark.asyncio
    async def test_send_raises_when_busy(self):
        from agent import Agent
        from service.inprocess import InProcessAgentService

        agent = Agent(verbose=False)
        svc = InProcessAgentService(agent, session_id="test-busy")
        # Force busy state.
        svc._is_busy = True
        with pytest.raises(RuntimeError, match="busy"):
            await svc.send("hello")
        svc._is_busy = False
        await svc.close()


# ── Live integration tests (require API key) ──────────────────────────────────

_HAS_API_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
_SKIP_LIVE = pytest.mark.skipif(not _HAS_API_KEY, reason="ANTHROPIC_API_KEY not set")


@_SKIP_LIVE
class TestServiceLive:
    """End-to-end tests that hit the real LLM API."""

    @pytest.mark.asyncio
    async def test_simple_turn_event_sequence(self):
        """Verify: TurnStarted → content → MessageComplete → TurnCompleted."""
        from agent import Agent
        from service.inprocess import InProcessAgentService
        from runtime.events import init_runtime_events

        session_id = "test-live-sequence"
        init_runtime_events(session_id)

        agent = Agent(verbose=False)
        svc = InProcessAgentService(agent, session_id=session_id)

        received_types: list[str] = []
        async def collect_events():
            async for event in svc.events():
                received_types.append(event.type)
                if event.type == "turn.completed":
                    break

        collect_task = asyncio.create_task(collect_events())

        handle = await svc.send("Reply with exactly one word: 'hello'")
        response = await handle.wait()

        await collect_task
        await svc.close()

        assert "turn.started" in received_types
        assert "content.message_complete" in received_types
        assert "turn.completed" in received_types
        assert response.strip().lower().startswith("hello")

    @pytest.mark.asyncio
    async def test_handle_wait_returns_response(self):
        from agent import Agent
        from service.inprocess import InProcessAgentService
        from runtime.events import init_runtime_events

        session_id = "test-live-wait"
        init_runtime_events(session_id)

        agent = Agent(verbose=False)
        svc = InProcessAgentService(agent, session_id=session_id)

        handle = await svc.send("Say only: 'pong'")
        response = await handle.wait()
        assert response
        await svc.close()
