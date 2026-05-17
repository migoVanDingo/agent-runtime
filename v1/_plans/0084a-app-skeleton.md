# 0084a — App skeleton: prompt_toolkit full-screen Application

> **Context:** Part of the 0084 TUI rewrite. Replaces the old `PromptSession + patch_stdout`
> approach with `Application(full_screen=True)`. All sub-phases build on the layout and
> model files established here.

## Goal

Rewrite `src/ui/app.py` to use a full-screen `Application` with a fixed five-zone layout.
Create the three model files as thin stubs. Slash commands handled entirely inside the TUI
without delegating to the old CLI command registry.

## Files changed

| File | Change |
|------|--------|
| `src/ui/app.py` | Full rewrite — `Application(full_screen=True, mouse_support=False)` |
| `src/ui/conversation.py` | Created — `ConversationModel` |
| `src/ui/spinner_model.py` | Created — `SpinnerModel` |
| `src/ui/input_model.py` | Created — `InputModel` |

## Key implementation notes

**Layout zones (top to bottom):**

```
conv_window      — scrollable conversation, fills remaining height
spinner_window   — ConditionalContainer (hidden when inactive), height=1
separator        — Window(height=1, char="─", style="ansigray")
input_window     — BufferControl, height=D(min=1, max=5)
footer           — Window(height=1), bg:#1a1a1a
```

**`mouse_support=False`:** Deliberate. Enables native terminal text selection
(click-drag copy) in iTerm2/Terminal.app. With `mouse_support=True` prompt_toolkit
captures mouse events and selection breaks.

**Slash commands** are handled inline in `_execute_command()`. There is no delegation
to the old `arc` CLI command registry. `/clear` directly mutates `conv._chunks` and
`conv._cursor_idx` as it is a TUI-internal operation with no service-layer counterpart.

**Banner timing:** `_print_banner(info)` writes to real terminal stdout *before*
`app.run_async()` activates the alternate screen, so the banner persists in scrollback.

**`_SuppressStderr`** context manager redirects `sys.stderr` to
`<session_dir>/logs/stderr.log` for the TUI session lifetime, preventing subprocess
warnings (HuggingFace tokenizers, etc.) from garbling the alternate screen.

**Key bindings:**

| Key | Action |
|-----|--------|
| `Enter` | `buffer.validate_and_handle()` — submit |
| `Shift+Enter` | `buffer.newline()` — insert newline |
| `Ctrl+D` | Exit |
| `Ctrl+C` | No-op (prevents accidental quit) |
| `Escape` | Toggle pause/resume |
| `PageUp/Down` | Scroll conversation ±10 lines |

Up/down arrow navigation within the multi-line input buffer is native to prompt_toolkit
— no custom binding needed.

**`_interactive()` startup sequence:**
1. Create models (`ConversationModel`, `SpinnerModel`, `InputModel`)
2. Wire gates onto `input_model`
3. Call `_print_banner(info)`
4. Build application via `_build_app()`
5. If `--resume`: call `_handle_resume()` to populate conv before TUI starts
6. Launch background tasks (`_consume_events`, `_spinner_tick`, `_escalation_watcher`)
7. Enter `_SuppressStderr` and `await app.run_async()`
8. Cancel tasks and call `service.close()`

## Verification

```bash
cd /Users/bubz/Developer/agent/runtime/agent-runtime
python3 -c "from ui.app import run; from ui.conversation import ConversationModel; from ui.spinner_model import SpinnerModel; from ui.input_model import InputModel; print('OK')"
python3 -m pytest tests/integration/test_service.py -q --no-header
```

## Done when

- [ ] `src/ui/app.py` uses `Application(full_screen=True, mouse_support=False)`
- [ ] `_build_app()` returns `(Application, Buffer)` with the five-zone layout
- [ ] `_SuppressStderr` redirects stderr to `<session_dir>/logs/stderr.log`
- [ ] `_print_banner()` called before `app.run_async()`
- [ ] `_execute_command()` handles `/help`, `/pause`, `/resume`, `/cancel`, `/clear`, `/settings`, `/exit`
- [ ] Import check passes without error
- [ ] Integration tests still green
