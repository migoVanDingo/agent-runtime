# 0084h — Spinner parameter removal from pipeline stages

> **Depends on:** 0084a–g. The TUI now drives spinner updates entirely through
> `AgentEvent`s (`stage.started`, `tool.call.started`). The `spinner` object
> no longer needs to be threaded through every stage constructor.

## Goal

Remove the `spinner` parameter from all pipeline stage `__init__` signatures, from
`ToolLoop`, `ToolCallExecutor`, and `injection_gate.handle_injection_warning()`.
Update `agent.py` `_build_pipeline` to not pass `spinner=`. Update the unit test that
was constructing stages with `spinner=`.

## Files changed

| File | Change |
|------|--------|
| `src/runtime/stages/planning.py` | Removed `spinner` param from `__init__` |
| `src/runtime/stages/synthesizer.py` | Removed `spinner` param from `__init__` |
| `src/runtime/stages/skill_hint.py` | Removed `spinner` param from `__init__` |
| `src/runtime/stages/continuation.py` | Removed `spinner` param from `__init__` |
| `src/runtime/stages/council.py` | Removed `spinner` param from `__init__` |
| `src/runtime/stages/execution.py` | Removed `spinner` param from `__init__` |
| `src/runtime/stages/direct_execution.py` | Removed `spinner` param from `__init__` |
| `src/runtime/tool_loop.py` | Removed `spinner` param |
| `src/runtime/tool_executor.py` | Removed `spinner` param |
| `src/runtime/injection_gate.py` | Removed `spinner` from `handle_injection_warning()` |
| `src/agent.py` | `_build_pipeline` no longer passes `spinner=` to any stage |
| `tests/unit/test_tool_loop.py` | Updated to not pass `spinner=` |

## Key implementation notes

**`NoopSpinner` stays in `service/inprocess.py`:** The legacy CLI path still uses
`agent.spinner` for its own display. `InProcessAgentService.__init__()` replaces
`agent.spinner` with a `NoopSpinner` instance so any residual `agent.spinner.start()`
calls in non-stage code are silenced without error.

**Event-driven replacement:** Stage progress is now communicated exclusively via the
`RuntimeEvent` bus (`stage.started`, `stage.completed`, `tool.call.started`, etc.),
which `InProcessAgentService` translates into `AgentEvent`s and delivers to the TUI.
No spinner object is needed inside any stage.

**`injection_gate.handle_injection_warning()`:** The `spinner` argument was previously
used to pause the spinner animation during a blocking injection prompt. With the TUI's
`TUIUserGate` handling all blocking prompts on the worker thread, the injection gate no
longer needs to touch any spinner.

**Unit test update:** `tests/unit/test_tool_loop.py` was constructing `ToolLoop` or
stage instances with `spinner=MockSpinner()`. These calls are updated to remove the
`spinner` keyword argument.

## Verification

```bash
cd /Users/bubz/Developer/agent/runtime/agent-runtime
# Should produce no output (no spinner self-references remain in stages/tool_loop/tool_executor)
grep -rn "self._spinner" src/runtime/stages/ src/runtime/tool_loop.py src/runtime/tool_executor.py 2>/dev/null | grep -v __pycache__

python3 -m pytest tests/unit/test_tool_loop.py -q --no-header
python3 -m pytest tests/integration/test_service.py -q --no-header
```

## Done when

- [ ] `spinner` parameter removed from all 7 stage `__init__` signatures
- [ ] `spinner` parameter removed from `ToolLoop` and `ToolCallExecutor`
- [ ] `spinner` parameter removed from `handle_injection_warning()`
- [ ] `agent.py` `_build_pipeline` passes no `spinner=` argument to any stage
- [ ] `tests/unit/test_tool_loop.py` updated to not pass `spinner=`
- [ ] `grep -rn "self._spinner" src/runtime/stages/ src/runtime/tool_loop.py src/runtime/tool_executor.py` returns empty
- [ ] `NoopSpinner` still present in `service/inprocess.py` for legacy CLI path
- [ ] Unit and integration tests still green
