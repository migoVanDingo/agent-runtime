# 0084i — TUIInputGate: ASK_USER clarification in TUI

> **Depends on:** 0084a–f. Parallel to `TUIUserGate` (escalation), but for in-turn
> clarification questions emitted by pipeline tools via `ASK_USER`.

## Goal

Add `TUIInputGate` to `service/inprocess.py`. Wire it as `pipeline._user_input_fn` in
`builder.py`. In `app.py`, route user input to `igate.supply_answer()` when a question
is pending, and display the question in the conversation via `_escalation_watcher()`.

## Files changed

| File | Change |
|------|--------|
| `src/service/inprocess.py` | `TUIInputGate` class added |
| `src/service/__init__.py` | `TUIInputGate` re-exported |
| `src/service/builder.py` | Wire `input_gate.ask` as `pipeline._user_input_fn` |
| `src/ui/app.py` | `input_model.input_gate` wired; routing in `_handle_input()`; question display in `_escalation_watcher()` |
| `src/ui/input_model.py` | `input_gate` field added; prefix/footer branches for clarification |

## Key implementation notes

**`TUIInputGate` threading model** (mirrors `TUIUserGate`):

```python
class TUIInputGate:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._answer: str = ""
        self.pending_question: str | None = None

    def ask(self, question: str) -> str:
        """Worker thread blocks here until supply_answer() is called."""
        self.pending_question = question
        self._event.clear()
        self._event.wait()        # blocks worker thread
        self.pending_question = None
        return self._answer

    def supply_answer(self, text: str) -> None:
        """Called from TUI (event loop thread). Unblocks worker."""
        self._answer = text
        self._event.set()
```

**`builder.py` wiring:**

```python
input_gate = TUIInputGate()
service.input_gate = input_gate
agent._pipeline._user_input_fn = input_gate.ask
```

`_user_input_fn` is the callable the pipeline invokes when a tool calls `ASK_USER`.
Replacing the old `_tui_safe_input` function with `input_gate.ask` removes the last
direct stdout/stdin interaction from the worker thread path.

**`_handle_input()` routing (in `app.py`):**

Priority order (before normal send):
1. Slash command
2. Escalation gate pending (`y`/`n` → `gate.supply_answer()`)
3. **Input gate pending** → `igate.supply_answer(text)`, show grey confirmation
4. Service busy → queue
5. Normal send

**`_escalation_watcher()` extension:** Same watcher polls `igate.pending_question`.
When a new question object is detected:
```python
conv.add("ansiyellow bold", f"\n❓  {q}\n")
conv.add("ansigray", "  (Type your clarification and press Enter)\n\n")
```
`shown_q` tracks the last displayed question to avoid re-rendering on every 100 ms tick.

**`InputModel.get_prompt_prefix()` / `get_footer_text()`:** Both check
`input_gate.pending_question` first (priority over escalation) and return yellow
clarification hints.

## Verification

```bash
cd /Users/bubz/Developer/agent/runtime/agent-runtime
python3 -c "
from service.inprocess import TUIInputGate
g = TUIInputGate()
assert g.pending_question is None
print('TUIInputGate OK')
"
python3 -c "from service import TUIInputGate; print('export OK')"
python3 -m pytest tests/integration/test_service.py -q --no-header
```

## Done when

- [ ] `TUIInputGate` class in `src/service/inprocess.py` with `ask()` / `supply_answer()` / `pending_question`
- [ ] `TUIInputGate` exported from `src/service/__init__.py`
- [ ] `builder.py` creates `TUIInputGate`, sets `service.input_gate`, and wires `pipeline._user_input_fn`
- [ ] `app.py` sets `input_model.input_gate = getattr(service, "input_gate", None)`
- [ ] `_handle_input()` routes to `igate.supply_answer(text)` when `igate.pending_question` is set
- [ ] `_escalation_watcher()` displays the question in conversation exactly once per unique question
- [ ] `InputModel.get_prompt_prefix()` shows yellow `Clarify:` prefix when question pending
- [ ] Integration tests still green
