# 0084b — ConversationModel

> **Depends on:** 0084a (app skeleton). The layout's `FormattedTextControl` calls
> `conv.get_formatted_text()` on every redraw.

## Goal

Implement `ConversationModel` in `src/ui/conversation.py`. Stores all conversation content
as a flat list of `(style, text)` tuples and provides the `[SetCursorPosition]` token
mechanism that drives Window scrolling without any custom scroll math.

## Files changed

| File | Change |
|------|--------|
| `src/ui/conversation.py` | Full implementation of `ConversationModel` |

## Key implementation notes

**Storage:** `_chunks: list[tuple[str, str]]`. Every piece of text — user messages,
agent responses, errors, timers — is appended as one or more tuples. Rich-rendered ANSI
from `finalize_agent_response()` is exploded into tuples via
`list(to_formatted_text(ANSI(ansi_text)))` before appending.

**Scrolling via `[SetCursorPosition]`:** prompt_toolkit scrolls a `Window` to keep the
cursor in view. Inserting `("[SetCursorPosition]", "")` at index `_cursor_idx` in
`get_formatted_text()` hijacks that mechanism to implement page-up/page-down without any
scroll offset state. When `_auto_scroll=True` the cursor stays at the end (bottom-pinned).
When `_auto_scroll=False` the cursor is placed at `_cursor_idx`, locking the viewport.

**Scroll methods:**
- `scroll_up(10)` → `_cursor_idx -= lines * 3`, `_auto_scroll = False`
- `scroll_down(10)` → if new idx >= len(chunks), re-enable `_auto_scroll`; else advance

The `* 3` multiplier approximates wrapped-line height — one logical chunk often renders
as multiple terminal lines due to word-wrap.

**Streaming:** `append_token(text)` accumulates into `_stream_text` (not into `_chunks`).
`get_formatted_text()` appends the live stream text at the end so it renders immediately.
`finalize_agent_response(full_text)` clears `_stream_text`, renders Markdown via Rich,
and appends the result into `_chunks`.

**Markdown rendering:** `_render_markdown_to_ansi()` creates a `rich.Console` backed by
a `StringIO` buffer (`force_terminal=True`) so Rich emits ANSI codes without touching
stdout.

**Public API:**

| Method | Purpose |
|--------|---------|
| `add(style, text)` | Append a styled chunk |
| `add_ansi(ansi_text)` | Parse ANSI and append multiple chunks |
| `add_user_message(text)` | Green bold `▶` prefix then raw text |
| `begin_agent_response()` | Print "Agent" header; reset stream buffer |
| `append_token(text)` | Accumulate streaming token (not yet in chunks) |
| `finalize_agent_response(full_text)` | Render Markdown, commit to chunks |
| `add_timer(elapsed_ms)` | Gray `⏱ M:SS` line |
| `add_error(error)` | Red "Error:" line |
| `add_cancelled()` | Yellow "Turn cancelled." line |
| `add_escalation(esc)` | Red escalation block with tool name and input fields |

## Verification

```bash
cd /Users/bubz/Developer/agent/runtime/agent-runtime
python3 -c "from ui.conversation import ConversationModel; c = ConversationModel(); c.add_user_message('hi'); c.begin_agent_response(); c.append_token('hello'); c.finalize_agent_response('hello'); print(len(c.get_formatted_text()), 'chunks OK')"
python3 -m pytest tests/integration/test_service.py -q --no-header
```

## Done when

- [ ] `ConversationModel` in `src/ui/conversation.py`
- [ ] `get_formatted_text()` inserts `[SetCursorPosition]` at `_cursor_idx` (or end when auto-scroll)
- [ ] `scroll_up()` / `scroll_down()` move `_cursor_idx`; `scroll_down()` to end re-enables `_auto_scroll`
- [ ] `append_token()` accumulates in `_stream_text`; rendered live in `get_formatted_text()`
- [ ] `finalize_agent_response()` renders Markdown via Rich into `_chunks` (clears `_stream_text`)
- [ ] `add_escalation()` shows reason, tool name, and up to 4 input key/value pairs
- [ ] Integration tests still green
