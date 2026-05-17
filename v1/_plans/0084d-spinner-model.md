# 0084d — SpinnerModel

> **Depends on:** 0084a (layout wires `spinner_window` with `ConditionalContainer`).

## Goal

Implement `SpinnerModel` in `src/ui/spinner_model.py`. Provides animated dots with color
pulsing that display the current agent stage label. Visibility driven by `spinner.active`
via `ConditionalContainer` — no manual hide/show calls in the layout code.

## Files changed

| File | Change |
|------|--------|
| `src/ui/spinner_model.py` | Full implementation of `SpinnerModel` |

## Key implementation notes

**Animation constants:**

```python
_DOTS   = ["·", "··", "···"]
_COLORS = ["ansicyan", "ansibrightcyan", "ansicyan", "ansigray"]
```

Three dot states cycle at 0.4 s per frame (driven by `_spinner_tick()` in `app.py`).
Four color states create a pulse that is slightly out of phase with the dot cycle,
producing a subtle shimmer without additional complexity.

**`get_formatted_text()`** returns an empty `FormattedText([])` when `active=False`,
so the `ConditionalContainer` check and the content are in sync — both are falsy when
the spinner is hidden.

**`ConditionalContainer` wiring (in `_build_app()`):**

```python
ConditionalContainer(
    content=Window(content=FormattedTextControl(spinner.get_formatted_text), height=1),
    filter=Condition(lambda: spinner.active),
)
```

The container collapses to zero height when inactive, so the separator line rises to
meet the conversation window without any gap.

**Lifecycle:**

| Method | Caller | Effect |
|--------|--------|--------|
| `start(msg)` | `_consume_events` on `turn.started` | Sets `active=True`, resets `_frame=0` |
| `update(msg)` | `_consume_events` on `stage.started` / `tool.call.started` | Updates label only |
| `stop()` | `_consume_events` on completion/error/cancel | Sets `active=False` |
| `tick()` | `_spinner_tick` every 0.4 s | Increments `_frame` |

**Stage label mapping** lives in `app.py` (`_STAGE_LABELS` dict), not in `SpinnerModel`.
`SpinnerModel` is display-only; it holds no knowledge of event types or stage names.

## Verification

```bash
cd /Users/bubz/Developer/agent/runtime/agent-runtime
python3 -c "
from ui.spinner_model import SpinnerModel
s = SpinnerModel()
assert not s.active
s.start('Thinking')
assert s.active
s.tick(); s.tick()
ft = s.get_formatted_text()
assert len(ft) > 0
s.stop()
assert not s.active
assert s.get_formatted_text() == []
print('SpinnerModel OK')
"
python3 -m pytest tests/integration/test_service.py -q --no-header
```

## Done when

- [ ] `SpinnerModel` in `src/ui/spinner_model.py`
- [ ] `_DOTS = ["·", "··", "···"]` and `_COLORS = ["ansicyan", "ansibrightcyan", "ansicyan", "ansigray"]`
- [ ] `tick()` increments `_frame`; frame index wraps via `% len(_DOTS)` and `% len(_COLORS)`
- [ ] `get_formatted_text()` returns `FormattedText([])` when `active=False`
- [ ] `ConditionalContainer` in layout uses `Condition(lambda: spinner.active)`
- [ ] `_spinner_tick()` background task advances frame every 0.4 s and calls `app.invalidate()`
- [ ] Integration tests still green
