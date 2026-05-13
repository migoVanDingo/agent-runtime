# 0084c — Multiline input buffer

> **Depends on:** 0084a (layout wires the buffer into `input_window`),
> 0084b (`InputModel` provides the dynamic prompt prefix).

## Goal

Configure the input `Buffer` for multiline editing: `Enter` submits, `Shift+Enter`
inserts a newline. Dynamic prefix reflects current gate state. Input window grows up to
five lines before scrolling.

## Files changed

| File | Change |
|------|--------|
| `src/ui/app.py` | `_build_app()` — Buffer and key binding configuration |
| `src/ui/input_model.py` | `InputModel.get_prompt_prefix()` — dynamic prefix |

No new files. Changes are part of `_build_app()` in app.py.

## Key implementation notes

**Buffer configuration:**

```python
Buffer(
    name="input",
    multiline=True,
    accept_handler=_on_accept,
    history=InMemoryHistory(),
)
```

`accept_handler` receives the buffer after `validate_and_handle()` is called. It strips
whitespace, schedules `_handle_input()` as an asyncio task, then returns `True` to
clear the buffer. Returning `True` is required — returning `None` or `False` keeps the
text in the buffer.

**Key bindings:**

- `enter` → `event.current_buffer.validate_and_handle()` — triggers `accept_handler`
- `s-enter` → `event.current_buffer.newline()` — inserts `\n` at cursor

Up/down arrow navigation between lines within the buffer is handled natively by
prompt_toolkit's `BufferControl` — no custom binding is needed or wanted (adding one
would break intra-line cursor movement).

**Dynamic prompt prefix** (`BeforeInput`):

`BeforeInput(input_model.get_prompt_prefix)` calls `get_prompt_prefix()` on every
redraw. Priority order:

1. `input_gate.pending_question` set → yellow `Clarify:` prefix
2. `escalation_gate.pending_escalation` set → red `Allow? [y/n]` prefix
3. Normal → blue `▶` arrow

**Input window dimensions:**

```python
Window(height=D(min=1, max=5), dont_extend_height=True, wrap_lines=True)
```

Grows from one line to five as the user types; wraps long lines within the five-line
budget rather than truncating.

**Message queue:** If `service.is_busy`, `_handle_input()` calls
`input_model.queue_message(text)` and shows a grey `(queued)` notice. On
`turn.completed`, `_consume_events()` calls `input_model.pop_pending()` to drain
one queued message and dispatch it immediately.

## Verification

```bash
cd /Users/bubz/Developer/agent/runtime/agent-runtime
python3 -c "from ui.app import run; from ui.input_model import InputModel; m = InputModel(); print(m.get_prompt_prefix()); print('OK')"
python3 -m pytest tests/integration/test_service.py -q --no-header
```

## Done when

- [ ] `Buffer(multiline=True, accept_handler=_on_accept, history=InMemoryHistory())`
- [ ] `Enter` calls `buffer.validate_and_handle()`; `Shift+Enter` calls `buffer.newline()`
- [ ] `BeforeInput(input_model.get_prompt_prefix)` renders dynamic prefix
- [ ] `Window(height=D(min=1, max=5))` — input grows up to five lines
- [ ] `InputModel.get_prompt_prefix()` returns correct text for normal / escalation / clarification states
- [ ] `queue_message()` / `pop_pending()` round-trips work correctly
- [ ] Integration tests still green
