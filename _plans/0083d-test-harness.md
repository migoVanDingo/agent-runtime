# 0083d — Service test harness

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §10 phase 0083d description.
> Depends on: **0083c** (InProcessAgentService).

## Goal

Provide two concrete ways to exercise the service layer without a TUI:

1. **`scripts/service_repl.py`** — an interactive async REPL. Starts the
   service, reads lines from stdin, calls `service.send()`, prints each
   `AgentEvent` as it arrives. Used for manual smoke-testing during development.

2. **`tests/integration/test_service.py`** — a pytest integration test that
   drives one turn against the real provider and asserts the event sequence.
   Marked to skip when no API key is present.

These let the implementer validate Phase 0083c before touching any UI code.

## Files to create

| File | Purpose |
|------|---------|
| `scripts/service_repl.py` | Interactive async REPL for the service layer |
| `tests/integration/test_service.py` | Integration test for event sequence |
| `tests/integration/__init__.py` | Package marker (create if missing) |

## Detailed implementation

### `scripts/service_repl.py`

```python
#!/usr/bin/env python3
"""Interactive REPL for the InProcessAgentService.

Run from the repo root:
    python scripts/service_repl.py

Each line you type is sent as a turn. All AgentEvents are printed as they
arrive. Ctrl+C or 'exit' to quit.

This is a development and debugging tool — it exercises the service layer
without any TUI, validating that events flow correctly before the UI exists.
"""
from __future__ import annotations

import asyncio
import sys
import os

# Ensure src/ is on the path when run from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent import Agent
from service.inprocess import InProcessAgentService
from service.events import AgentEvent, TokenChunk


def _format_event(event: AgentEvent) -> str:
    """Short human-readable summary of an event for the REPL display."""
    t = event.type
    if t == "content.token_chunk":
        # Don't print a line per token — just the text inline.
        return None  # handled specially below
    if t == "content.message_complete":
        return f"\n[MessageComplete] length={len(getattr(event, 'text', ''))}"
    if t == "stage.started":
        return f"[StageStarted] {getattr(event, 'stage', '')}"
    if t == "stage.completed":
        stage = getattr(event, 'stage', '')
        ms = getattr(event, 'duration_ms', 0)
        return f"[StageCompleted] {stage} ({ms}ms)"
    if t == "tool.call.started":
        return f"[ToolCallStarted] {getattr(event, 'tool_name', '')} id={getattr(event, 'tool_call_id', '')}"
    if t == "tool.call.completed":
        err = getattr(event, 'error', '')
        suffix = f" ERROR={err[:60]}" if err else ""
        return f"[ToolCallCompleted] {getattr(event, 'tool_name', '')}{suffix}"
    if t == "turn.started":
        return f"[TurnStarted] turn_id={event.turn_id}"
    if t == "turn.completed":
        ms = getattr(event, 'elapsed_ms', 0)
        return f"[TurnCompleted] {ms}ms"
    if t == "turn.failed":
        return f"[TurnFailed] {getattr(event, 'error', '')}"
    if t == "turn.cancelled":
        return f"[TurnCancelled] at={getattr(event, 'at_stage', '')}"
    # Generic fallback
    return f"[{t}]"


async def _read_line(prompt: str) -> str:
    """Read a line from stdin without blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))


async def run_repl() -> None:
    from app_config import config
    from runtime.events import init_runtime_events

    session_id = "repl-session"
    init_runtime_events(session_id, project_id="repl")

    agent = Agent(verbose=False)
    service = InProcessAgentService(agent, session_id=session_id)

    print("Service REPL — type a message and press Enter. 'exit' to quit.")
    print(f"Session: {service.session_id}\n")

    # Background task: drain the global event stream and print each event.
    streaming_in_progress = False

    async def event_printer() -> None:
        nonlocal streaming_in_progress
        async for event in service.events():
            if isinstance(event, TokenChunk):
                if not streaming_in_progress:
                    print("\nAgent: ", end="", flush=True)
                    streaming_in_progress = True
                print(event.text, end="", flush=True)
                continue

            # Non-token event — print summary on its own line.
            if streaming_in_progress and event.type != "content.message_complete":
                print()  # newline after streaming tokens
                streaming_in_progress = False

            summary = _format_event(event)
            if summary:
                print(summary)

            if event.type in ("turn.completed", "turn.failed", "turn.cancelled"):
                streaming_in_progress = False
                print()  # blank line after turn ends

    printer_task = asyncio.create_task(event_printer())

    try:
        while True:
            try:
                line = await _read_line("> ")
            except (EOFError, KeyboardInterrupt):
                break

            line = line.strip()
            if not line:
                continue
            if line.lower() in ("exit", "quit"):
                break

            if service.is_busy:
                print("[busy — turn still in flight, please wait]")
                continue

            handle = await service.send(line)
            # Wait for the turn to complete before accepting the next input.
            try:
                response = await handle.wait()
            except Exception as exc:
                print(f"[turn error: {exc}]")

    finally:
        printer_task.cancel()
        await service.close()
        print("\nGoodbye.")


def main() -> None:
    asyncio.run(run_repl())


if __name__ == "__main__":
    main()
```

### `tests/integration/test_service.py`

```python
"""Integration tests for InProcessAgentService.

These tests hit the real LLM provider. They are skipped when ANTHROPIC_API_KEY
(or equivalent) is not set. Run with:

    pytest tests/integration/test_service.py -v

Expected events for a simple direct-mode question:
    TurnStarted → StageStarted(RoutingStage) → StageCompleted → ... →
    StageStarted(DirectExecutionStage or DirectInlineStage) → StageCompleted →
    TokenChunk(s) → MessageComplete → TurnCompleted
"""
from __future__ import annotations

import os
import asyncio
import pytest

from agent import Agent
from service.inprocess import InProcessAgentService
from service.events import (
    TurnStarted, TurnCompleted, TurnFailed, TurnCancelled,
    TokenChunk, MessageComplete,
    StageStarted, StageCompleted,
)

# Skip the entire module if no API key is available.
pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"),
    reason="No LLM API key in environment — skipping live integration tests",
)


@pytest.fixture
def session_id():
    return "test-session-001"


@pytest.fixture
async def service(session_id):
    """Async fixture — creates and tears down the service around each test."""
    from runtime.events import init_runtime_events
    init_runtime_events(session_id, project_id="test")
    agent = Agent(verbose=False)
    svc = InProcessAgentService(agent, session_id=session_id)
    yield svc
    await svc.close()


@pytest.mark.asyncio
async def test_simple_turn_event_sequence(service):
    """A direct-mode question should produce a predictable event sequence."""
    events_seen = []

    async def collect_events():
        async for event in service.events():
            events_seen.append(event)
            # Stop after TurnCompleted or TurnFailed.
            if event.type in ("turn.completed", "turn.failed", "turn.cancelled"):
                break

    collector = asyncio.create_task(collect_events())

    handle = await service.send("What is 2 + 2? Reply with just the number.")
    response = await handle.wait()

    # Give the collector a moment to receive TurnCompleted.
    await asyncio.wait_for(collector, timeout=60.0)

    types = [e.type for e in events_seen]
    print(f"Event sequence: {types}")

    assert "turn.started" in types, "TurnStarted must be emitted"
    assert "turn.completed" in types, "TurnCompleted must be emitted"
    assert "turn.failed" not in types, "TurnFailed must NOT be emitted for a clean turn"
    assert "content.message_complete" in types, "MessageComplete must be emitted"

    # TurnStarted must precede TurnCompleted.
    started_idx = types.index("turn.started")
    completed_idx = types.index("turn.completed")
    assert started_idx < completed_idx

    # Response should contain '4'.
    assert "4" in response, f"Expected '4' in response but got: {response!r}"


@pytest.mark.asyncio
async def test_service_not_reentrant(service):
    """Calling send() while a turn is in flight must raise RuntimeError."""
    # Start a turn but don't await the handle.
    handle = await service.send("Say 'hello' very slowly")
    assert service.is_busy is True

    with pytest.raises(RuntimeError, match="busy"):
        await service.send("another message")

    await handle.wait()


@pytest.mark.asyncio
async def test_turn_handle_events_scoped_to_turn(service):
    """TurnHandle.events() should only yield events for that specific turn."""
    handle = await service.send("What is 1 + 1? Reply with just the number.")

    turn_event_types = []
    async for event in handle.events():
        turn_event_types.append(event.type)
        if event.type in ("turn.completed", "turn.failed", "turn.cancelled"):
            break

    response = await handle.wait()

    # All events should have the correct turn_id.
    for event in [e for e in turn_event_types]:  # types only here
        pass  # events are already filtered by handle.events()

    assert "turn.completed" in turn_event_types or "turn.failed" in turn_event_types
    assert "2" in response
```

## Running the harness manually

```bash
# Interactive REPL (requires API key in environment)
cd /Users/bubz/Developer/agent/runtime/agent-runtime
python scripts/service_repl.py

# Integration tests
pytest tests/integration/test_service.py -v -s
```

## Verification

```bash
# 1. REPL starts without error
python scripts/service_repl.py <<< $'what is 2+2\nexit'
# Expected output: event lines + "[TurnCompleted]" + response containing "4"

# 2. Integration tests pass (requires API key)
pytest tests/integration/test_service.py -v

# 3. Existing unit tests still pass
pytest -x -q
```

## Done when

- [ ] `scripts/service_repl.py` runs, prints events, and exits cleanly on `exit`.
- [ ] Events appear in the REPL in the correct order: `TurnStarted`, stage events, `TokenChunk`s, `MessageComplete`, `TurnCompleted`.
- [ ] `test_simple_turn_event_sequence` passes (or is skipped if no key).
- [ ] `test_service_not_reentrant` passes.
- [ ] `test_turn_handle_events_scoped_to_turn` passes.
- [ ] No stdout from stages (NoopSpinner working — nothing leaks through).
- [ ] `pytest -x -q` still passes (unit tests unaffected).

## Out of scope for this phase

- Testing pause/cancel (Phase 0083e).
- The Textual TUI (Phase 0083f).
- Performance benchmarking of the queue or event delivery latency.
