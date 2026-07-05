# 03 — tool-call cap emits results for every tool_use

**Mitigates:** `02-security-audit.md` M2 (also `03-code-quality.md` correctness
smells). A real correctness bug that fails turns, not just a style nit.

## The problem
`runtime/loop.py` appends the assistant message with **all** of a response's
`tool_use` blocks, then dispatches them in a loop. When the per-turn tool-call
cap (`max_tool_calls_per_turn`, default 30) tripped *mid-batch*, the loop
injected the cap message and `break`ed — leaving the remaining `tool_use` blocks
with no matching `tool_result`. Providers pair results to calls **by order**, so
the next request carried a `tool_use` with no result → provider **400
INVALID_ARGUMENT**, which the retry loop just repeated until the turn failed.

## The fix
`v2/src/arc/runtime/loop.py` — when the cap trips at block `i`, emit a synthetic
"skipped" `tool_result` for every remaining block (`tool_uses[i:]`) *before* the
cap message, so counts stay matched and no `tool_use` dangles:

```python
for rem in tool_uses[i:]:
    self._messages.append(Message(role="tool", content=[{
        "function_response": {"name": rem.tool_name or "",
            "response": {"result": "skipped: tool-call cap reached for this turn"}}}],
        name=rem.tool_name or ""))
```

`n_tool_calls` is unchanged (synthetic results aren't executions), so the
existing cap semantics hold.

## Verification
- New unit test `test_tool_call_cap_emits_result_for_every_tool_use` — 3 tool_uses,
  cap 2; asserts exactly 3 tool-role messages follow the assistant message (2
  executed + 1 synthetic) and one is marked `skipped`.
- Existing `test_tool_call_cap_stops_dispatch_within_iteration` still passes
  (`n_tool_calls == 2`).

## Residual
None for the dangling-`tool_use` class. The iteration cap (a separate limit) was
already correct.
