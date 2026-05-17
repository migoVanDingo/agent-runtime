# 0084f — Escalation gate wiring

> **Depends on:** 0084a–e. `TUIUserGate` already exists in `service/inprocess.py` from
> 0083. This phase wires it into the TUI display and input routing.

## Goal

Surface a pending escalation to the user: render the escalation details in the
conversation, change the prompt prefix and footer to approval hints, and route `y`/`n`
input directly to `gate.supply_answer()` rather than `service.send()`.

## Files changed

| File | Change |
|------|--------|
| `src/ui/input_model.py` | `get_prompt_prefix()` and `get_footer_text()` escalation branches |
| `src/ui/conversation.py` | `add_escalation(esc)` method |
| `src/ui/app.py` | `_handle_input()` escalation routing; `_escalation_watcher()` injection |

## Key implementation notes

**`InputModel.get_prompt_prefix()`** checks gates in priority order:
1. `input_gate.pending_question` → yellow `"  Clarify:  "`
2. `escalation_gate.pending_escalation` → red `"  Allow? [y/n]  "`
3. Normal → blue `"  ▶  "`

Priority 1 before 2: ASK_USER clarification takes precedence over a concurrent escalation
(both should not occur simultaneously, but the order prevents silent routing errors).

**`InputModel.get_footer_text()`** mirrors the same priority order:
- Clarification pending → yellow `"  ❓  Clarification needed  —  type your response and press Enter"`
- Escalation pending → red `"  ⚠  ESCALATION  —  type  y  to allow  or  n  to deny"`
- Normal → gray hint line

**`ConversationModel.add_escalation(esc)`** renders:
```
⚠  ESCALATION — <source>
  <reason>
  Tool:  <tool_name>       (omitted if empty)
  <key>:  <value>          (up to 4 input fields, truncated at 80 chars)
```
`esc.tool_input` is a dict; at most 4 entries are shown to keep the block concise.

**`_handle_input()` routing** (in `app.py`):
1. Slash command → `_execute_command()`
2. Escalation gate pending → `y`/`yes` → `gate.supply_answer(True)`; `n`/`no` →
   `gate.supply_answer(False)`; anything else → hint to user. `return` — never reaches service.
3. ASK_USER gate pending → `igate.supply_answer(text)`. `return`.
4. Service busy → queue message.
5. Normal → `conv.add_user_message(text)` + `service.send(text)`.

**`_escalation_watcher()`** detects `gate.pending_escalation is not None` and calls
`conv.add_escalation(esc)` once per unique escalation object (tracked via `shown_esc`
identity comparison). This means the worker thread sets `pending_escalation` and the
watcher picks it up within 100 ms without any additional event emission.

## Verification

```bash
cd /Users/bubz/Developer/agent/runtime/agent-runtime
python3 -c "
from ui.input_model import InputModel
m = InputModel()
# Normal state
ft = m.get_prompt_prefix()
assert 'blue' in str(ft) or '▶' in str(ft), str(ft)
print('InputModel escalation prefix OK')
"
python3 -m pytest tests/integration/test_service.py -q --no-header
```

## Done when

- [ ] `InputModel.get_prompt_prefix()` returns red `Allow? [y/n]` when escalation pending
- [ ] `InputModel.get_footer_text()` returns red escalation hint when escalation pending
- [ ] `ConversationModel.add_escalation(esc)` renders reason, tool name, and ≤4 input fields
- [ ] `_handle_input()` routes `y`/`n` to `gate.supply_answer()` when escalation pending
- [ ] `_escalation_watcher()` calls `conv.add_escalation(esc)` exactly once per escalation
- [ ] Integration tests still green
