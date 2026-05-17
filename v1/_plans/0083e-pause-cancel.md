# 0083e — Pause / cancel yield points

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §3.6.
> Depends on: **0083c** (InProcessAgentService — stubs replaced here).

## Goal

Implement cooperative pause and cancel. The worker thread running `agent.call()`
calls `checkpoint()` at three yield points; `checkpoint()` blocks on pause and
raises `TurnCancelledError` on cancel.

The three yield points:
1. **Between pipeline stages** — top of `Pipeline._run_stage()` before stage runs.
2. **Between tool-loop iterations** — top of the `while True:` loop in
   `ToolLoop.run()`, after `iteration += 1`.
3. **Streaming not checkpointed at the chunk level** — see note below.

**Streaming note from design doc §12:** Pausing mid-stream would stall the LLM
TCP connection. The checkpoint is intentionally NOT inserted inside the token
streaming loop. The effective pause granularity is "between tool invocations"
and "between pipeline stages" — responsive enough for the TUI use case. If the
user hits ESC while a streaming response is flowing, the pause takes effect
after the current stream completes.

## Files to modify

| File | Change |
|------|--------|
| `src/runtime/pipeline.py` | Add checkpoint call at top of `_run_stage()` |
| `src/runtime/tool_loop.py` | Add checkpoint call at top of `while True:` loop |
| `src/runtime/pipeline_context.py` | Add `_service_checkpoint` field |
| `src/service/inprocess.py` | Implement `pause()`, `resume()`, `cancel_current_turn()` |

## New exception type

Add `TurnCancelledError` to `src/service/errors.py` if not already done in
Phase 0083a. It must be importable from both `service.errors` and `service`:

```python
class TurnCancelledError(Exception):
    """Raised at a checkpoint when the current turn is cancelled.

    Raised on the worker thread; propagates up through agent.call() and is
    caught by InProcessAgentService._run_turn().
    """
    def __init__(self, at_stage: str = "") -> None:
        self.at_stage = at_stage
        super().__init__(f"Turn cancelled at: {at_stage}" if at_stage else "Turn cancelled")
```

## Detailed changes

### 1. `src/runtime/pipeline_context.py` — add checkpoint field

Read this file first to confirm the exact `PipelineContext` dataclass
definition. Then add one optional field:

```python
from typing import Callable

@dataclass
class PipelineContext:
    # ... existing fields unchanged ...

    # Set by InProcessAgentService before running a turn.
    # Called between pipeline stages and between tool-loop iterations.
    # None when running under the legacy CLI (no service layer).
    # TODO(0083-cleanup): consider a more formal injection mechanism.
    _service_checkpoint: Callable[[], None] | None = field(default=None, repr=False)
```

If `PipelineContext` uses `__init__` parameters directly (not a dataclass),
add `_service_checkpoint: Callable[[], None] | None = None` as an attribute
set in `__init__`. Either way, it must default to `None` so all existing
construction sites are unaffected.

### 2. `src/runtime/pipeline.py` — checkpoint between stages

In `Pipeline._run_stage()`, insert the checkpoint call before invoking
`stage.run(context)`. The method currently looks like:

```python
def _run_stage(self, stage, context, ask_counts):
    ...
    while True:
        result = stage.run(context)  # ← checkpoint goes just before this
        ...
```

Add:

```python
def _run_stage(self, stage, context, ask_counts):
    ...
    while True:
        # Cooperative yield point — gives InProcessAgentService a chance
        # to pause or cancel between stage invocations (including retries).
        if context._service_checkpoint is not None:
            context._service_checkpoint()   # may raise TurnCancelledError

        result = stage.run(context)
        ...
```

`TurnCancelledError` propagates up through `_run_stage` → `run()` → `agent.call()`
→ back to `InProcessAgentService._run_turn()` which catches it and emits
`TurnCancelled`.

### 3. `src/runtime/tool_loop.py` — checkpoint between iterations

In `ToolLoop.run()`, the main loop starts with `while True:` followed immediately
by `iteration += 1`. The checkpoint goes between them:

```python
while True:
    iteration += 1

    # Cooperative yield point — gives the service a chance to pause or
    # cancel between every tool invocation.
    if self._checkpoint is not None:
        self._checkpoint()   # may raise TurnCancelledError

    if iteration > cfg.max_iterations:
        ...
```

This requires adding `_checkpoint` to `ToolLoop.__init__`. Add an optional
`checkpoint: Callable[[], None] | None = None` parameter:

```python
def __init__(
    self,
    provider,
    messenger,
    context_mgr,
    tool_executor,
    spinner,
    user_gate,
    config,
    parent_identity=None,
    checkpoint=None,   # ← NEW — set by service layer
) -> None:
    ...
    self._checkpoint = checkpoint
```

`ToolLoop` is constructed inside stages. The stages need access to the
checkpoint function. The cleanest path: pull it from `PipelineContext` when
constructing `ToolLoop` inside `ExecutionStage` and `DirectExecutionStage`.
Read those stage files and add `checkpoint=context._service_checkpoint` to
the `ToolLoop(...)` constructor call.

### 4. `src/service/inprocess.py` — implement pause/cancel

Replace the three stub methods with real implementations.

```python
# ── Threading state (add to __init__) ────────────────────────────────────────

def __init__(self, agent, session_id):
    ...  # existing init code

    # Pause/cancel use threading primitives because agent.call() runs on a
    # worker thread, not the event loop. threading.Event is safe to set/clear
    # from the async side and wait on from the sync worker side.
    self._pause_event = threading.Event()
    self._pause_event.set()   # set = running (not paused)
    self._cancel_event = threading.Event()


# ── Checkpoint (called from worker thread) ────────────────────────────────────

def checkpoint(self) -> None:
    """Called synchronously from the worker thread at yield points.

    Checks for cancel first (fast path). Then blocks on the pause event if
    paused. Returns normally to continue; raises TurnCancelledError to abort.

    This method must never be called from the event loop — it blocks.
    """
    if self._cancel_event.is_set():
        self._cancel_event.clear()
        raise TurnCancelledError(at_stage="checkpoint")
    # Block here if paused. Waits with a timeout to remain interruptible
    # by subsequent cancel signals (checked again on each resume attempt).
    while not self._pause_event.wait(timeout=0.2):
        # Still paused — check for cancel again before going back to sleep.
        if self._cancel_event.is_set():
            self._cancel_event.clear()
            raise TurnCancelledError(at_stage="checkpoint-while-paused")


# ── Pause / cancel (called from event loop) ───────────────────────────────────

async def pause(self) -> None:
    """Request that the worker thread pause at the next checkpoint.

    Returns immediately. The worker thread may not pause for up to one
    tool-call or stage transition (whichever comes first).
    """
    self._pause_event.clear()   # threading.Event — safe from any thread

async def resume(self) -> None:
    """Resume a paused turn."""
    self._pause_event.set()

async def cancel_current_turn(self) -> None:
    """Cancel the in-flight turn.

    Sets the cancel flag, then unblocks the pause event in case the worker
    thread is sitting in checkpoint() waiting for a resume. The worker thread
    will see the cancel flag on its next checkpoint() call and raise
    TurnCancelledError.
    """
    if not self._is_busy:
        return
    self._cancel_event.set()
    self._pause_event.set()   # unblock paused worker so it can see the cancel
```

### 5. Wire checkpoint into the turn

In `InProcessAgentService._run_turn()`, before calling `run_in_executor`,
set the checkpoint on the `PipelineContext`. But `agent.call()` constructs
the context internally. The checkpoint needs to reach the context.

Two options:
- **Option A:** Set `agent._checkpoint = self.checkpoint` before each turn;
  the agent passes it into `PipelineContext` construction.
- **Option B:** Patch `agent` to accept a `checkpoint_fn` kwarg on `call()`.

Read `src/agent.py` to see how `PipelineContext` is constructed. The least
invasive: add `checkpoint_fn: Callable[[], None] | None = None` to
`agent.call()`, which passes it to `PipelineContext(... _service_checkpoint=checkpoint_fn)`.
The `InProcessAgentService` then calls:

```python
response = await loop.run_in_executor(
    self._executor,
    lambda: self._agent.call(message, on_token=on_token, checkpoint_fn=self.checkpoint),
)
```

And in `agent.call()`:

```python
def call(self, user_message: str, on_token=None, checkpoint_fn=None) -> str:
    ...
    context = PipelineContext(
        ...,
        _service_checkpoint=checkpoint_fn,
    )
    ...
```

## Verification

```bash
# 1. Existing tests still pass
pytest -x -q

# 2. Pause mid-turn (use the REPL from Phase 0083d)
# In one terminal, start the REPL and send a long-running task.
# From another shell, send SIGSTOP is not applicable here —
# use the async test below instead.

# 3. Automated pause/cancel test
python - <<'EOF'
import asyncio, time
from agent import Agent
from service.inprocess import InProcessAgentService
from service.errors import TurnCancelledError
from runtime.events import init_runtime_events

async def test_cancel():
    init_runtime_events("cancel-test", project_id="test")
    agent = Agent(verbose=False)
    svc = InProcessAgentService(agent, session_id="cancel-test")

    # Start a turn that will take a while.
    handle = await svc.send(
        "Count from 1 to 1000 using the bash_exec tool, "
        "printing each number with a sleep 0.01 between each."
    )
    assert svc.is_busy

    # Cancel after a short delay.
    await asyncio.sleep(1.5)
    await svc.cancel_current_turn()

    # The turn should raise TurnCancelledError.
    try:
        await asyncio.wait_for(handle.wait(), timeout=10.0)
        print("ERROR: expected TurnCancelledError but turn completed normally")
    except TurnCancelledError as exc:
        print(f"PASS: TurnCancelledError raised, at_stage={exc.at_stage!r}")
    except asyncio.TimeoutError:
        print("ERROR: turn did not cancel within 10s")
    finally:
        await svc.close()

asyncio.run(test_cancel())
EOF

# 4. Pause then resume
python - <<'EOF'
import asyncio, time
from agent import Agent
from service.inprocess import InProcessAgentService
from runtime.events import init_runtime_events
from service.events import TurnCompleted

async def test_pause_resume():
    init_runtime_events("pause-test", project_id="test")
    agent = Agent(verbose=False)
    svc = InProcessAgentService(agent, session_id="pause-test")

    events = []
    async def collect():
        async for e in svc.events():
            events.append(e)
            if e.type in ("turn.completed", "turn.failed", "turn.cancelled"):
                break

    collector = asyncio.create_task(collect())
    handle = await svc.send("What is 5 * 7? Reply with just the number.")

    # Pause immediately, then resume after 1 second.
    await svc.pause()
    print("Paused.")
    await asyncio.sleep(1.0)
    await svc.resume()
    print("Resumed.")

    result = await asyncio.wait_for(handle.wait(), timeout=30.0)
    await collector
    print(f"PASS: Got result: {result!r}")
    assert "35" in result, f"Expected '35' in result"
    await svc.close()

asyncio.run(test_pause_resume())
EOF
```

## Done when

- [ ] `PipelineContext` has `_service_checkpoint: Callable[[], None] | None = None`.
- [ ] `Pipeline._run_stage()` calls `context._service_checkpoint()` before each stage run.
- [ ] `ToolLoop.__init__` accepts `checkpoint` kwarg; `run()` calls it at top of each iteration.
- [ ] `ExecutionStage` and `DirectExecutionStage` pass `checkpoint=context._service_checkpoint` to `ToolLoop`.
- [ ] `checkpoint()` blocks on pause (threading.Event) and raises `TurnCancelledError` on cancel.
- [ ] `pause()` clears `_pause_event`; `resume()` sets it.
- [ ] `cancel_current_turn()` sets `_cancel_event` and unblocks `_pause_event`.
- [ ] `_run_turn()` catches `TurnCancelledError` and emits `TurnCancelled`.
- [ ] All existing tests pass — legacy CLI path is unaffected (checkpoint is None by default).

## Out of scope for this phase

- Stream-level pause (intentionally deferred — see streaming note above).
- The Textual TUI (Phase 0083f).
- ESC keybinding (Phase 0083h).
