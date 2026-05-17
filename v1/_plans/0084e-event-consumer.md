# 0084e — Event consumer and background tasks

> **Depends on:** 0084a–d (models and layout). This phase wires the live `service.events()`
> stream to the conversation and spinner models.

## Goal

Implement `_consume_events()`, `_spinner_tick()`, and `_escalation_watcher()` as the
three background asyncio tasks that drive all TUI updates after `app.run_async()` starts.

## Files changed

| File | Change |
|------|--------|
| `src/ui/app.py` | `_consume_events()`, `_spinner_tick()`, `_escalation_watcher()` added; wired in `_interactive()` |

## Key implementation notes

**`_consume_events()`** drains `service.events()` with a local `streaming: bool` flag:

| Event type | Action |
|-----------|--------|
| `turn.started` | `spinner.start("Thinking")`, reset `streaming=False` |
| `stage.started` | `spinner.update(_STAGE_LABELS.get(stage, stage))` |
| `tool.call.started` | `spinner.update(tool_name)` |
| `content.token_chunk` | First chunk: `spinner.stop()` + `conv.begin_agent_response()`. All: `conv.append_token(text)` |
| `content.message_complete` | `conv.finalize_agent_response(text)` (handles case where no chunks arrived) |
| `turn.completed` | `spinner.stop()`, `conv.add_timer(elapsed_ms)`, drain one queued message |
| `turn.failed` | `spinner.stop()`, `conv.add_error(error)` |
| `turn.cancelled` | `spinner.stop()`, `conv.add_cancelled()` |

Every branch calls `app.invalidate()` after updating state.

**`content.message_complete` without prior chunks:** If the agent returns a response
without streaming tokens (tool-only turns), `streaming` is still `False` when
`message_complete` arrives. The handler calls `conv.begin_agent_response()` before
`finalize_agent_response()` to ensure the "Agent" header is always printed.

**Queued message drain on `turn.completed`:** After updating the conversation,
`input_model.pop_pending()` is called once. If it returns a message, it is dispatched
immediately via `service.send()`. Only one message is drained per completion — the next
`turn.completed` drains the next one, preserving ordering without overloading the service.

**`_spinner_tick()`** is a simple `while True` loop:
```python
while True:
    if spinner.active:
        spinner.tick()
        app.invalidate()
    await asyncio.sleep(0.4)
```

**`_escalation_watcher()`** polls every 100 ms. It tracks `shown_esc` and `shown_q`
to call `conv.add_escalation(esc)` or inject the question only once per unique escalation
object, not repeatedly. Clearing (`esc is None`) resets `shown_esc` so a subsequent
escalation will trigger again.

**Task lifecycle:** All three tasks are created with `asyncio.create_task()` before
`app.run_async()`. After the app exits, tasks are cancelled and awaited with a 1 s
timeout via `asyncio.gather(..., return_exceptions=True)`.

## Verification

```bash
cd /Users/bubz/Developer/agent/runtime/agent-runtime
python3 -m pytest tests/integration/test_service.py -q --no-header
python3 -c "from ui.app import _consume_events, _spinner_tick, _escalation_watcher; print('tasks importable OK')"
```

## Done when

- [ ] `_consume_events()` routes all 8 relevant event types and calls `app.invalidate()`
- [ ] First `content.token_chunk` stops spinner and calls `conv.begin_agent_response()`
- [ ] `content.message_complete` handles the no-streaming case (calls `begin_agent_response()` if needed)
- [ ] `turn.completed` drains one pending message via `input_model.pop_pending()`
- [ ] `_spinner_tick()` advances frame every 0.4 s only when `spinner.active`
- [ ] `_escalation_watcher()` polls at 100 ms and adds each escalation to conv exactly once
- [ ] All three tasks cancelled gracefully on app exit (1 s timeout)
- [ ] Integration tests still green
