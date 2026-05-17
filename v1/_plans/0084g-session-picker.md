# 0084g — Session picker (--resume)

> **Depends on:** 0084a–f. Adds session listing to `InProcessAgentService` and populates
> the conversation model before the TUI starts when `--resume` is passed.

## Goal

Implement `--resume` session listing. Populate the conversation with a formatted table of
resumable sessions. `load_conversation()` allows restoring the last 30 messages from a
prior session into the messenger before the agent begins.

## Files changed

| File | Change |
|------|--------|
| `src/service/inprocess.py` | `list_resumable_sessions(limit)` and `load_conversation(session_id)` methods |
| `src/ui/app.py` | `_handle_resume()` coroutine; wired in `_interactive()` |

## Key implementation notes

**`list_resumable_sessions(limit=20)`** delegates to the artifact store:

```python
def list_resumable_sessions(self, limit: int = 20) -> list:
    from runtime.artifacts import get_artifact_store
    store = get_artifact_store()
    return store.list_resumable_sessions(limit=limit)
```

Returns a list of objects with at least `session_id`, `started_at` (Unix timestamp),
and `preview` (first ~50 chars of the first user message).

**`load_conversation(session_id)`** loads the last 30 messages from the artifact store
and injects them into the agent's messenger so the agent sees prior context:

```python
def load_conversation(self, session_id: str) -> list[dict]:
    from runtime.artifacts import get_artifact_store
    store = get_artifact_store()
    messages = store.load_conversation(session_id)
    ...  # inject into agent messenger
    return messages
```

**`_handle_resume()`** in `app.py` runs before `app.run_async()`:

1. Call `service.list_resumable_sessions(limit=20)`
2. If empty: `conv.add("ansigray", "No resumable sessions found. Starting fresh.\n\n")`
3. Otherwise: render a numbered list showing the full 16-char session ID, formatted date
   (`YYYY-MM-DD HH:MM`), and a 50-char preview

```
Resumable sessions:
  1  a1b2c3d4e5f6g7h8  |  2026-05-09 14:23  |  What is the status of...
  2  ...
```

4. Set `app_state["_resume_prompt"] = True` and prompt the user to select by number.

**Date formatting:** `datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M")`.
Timestamps are local time (no UTC conversion) to match the user's timezone.

**Selection UX:** The list is displayed in the conversation before the TUI input is active.
The user types a session number (or `q` to cancel) as their first message; `_handle_input()`
handles this as a normal input turn. No special modal state is needed.

## Verification

```bash
cd /Users/bubz/Developer/agent/runtime/agent-runtime
python3 -c "
from service.inprocess import InProcessAgentService
# Just check the method exists on the class
assert hasattr(InProcessAgentService, 'list_resumable_sessions')
assert hasattr(InProcessAgentService, 'load_conversation')
print('Session picker methods OK')
"
python3 -m pytest tests/integration/test_service.py -q --no-header
```

## Done when

- [ ] `InProcessAgentService.list_resumable_sessions(limit)` exists and delegates to artifact store
- [ ] `InProcessAgentService.load_conversation(session_id)` exists and injects messages into messenger
- [ ] `_handle_resume()` populates conversation with numbered session list before TUI starts
- [ ] Session list shows full 16-char session ID, formatted date, and 50-char preview
- [ ] Empty session list shows "No resumable sessions found" gracefully
- [ ] Integration tests still green
