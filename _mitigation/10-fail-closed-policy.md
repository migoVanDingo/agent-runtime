# 10 — policy hooks fail closed

**Mitigates:** `02-security-audit.md` H3 (security plugins fail open).

## The problem
In `HookRegistry.fire` (`bus.py`), an exception from a plugin's hook was
swallowed by `_record_failure` and the threaded value passed through unchanged.
For `before_tool_call` that meant a throwing `guard`/`safety_gate` left the
`ToolCall` untouched → **the tool executed** (silent policy bypass). And after
`failure_threshold` (3) throws the plugin was auto-quarantined (`_disabled`),
removing its policy for the rest of the session — so a reliably-throwing input
was a full enforcement bypass.

## The fix (`v2`)
- **Fail closed on a throwing gating hook** (`bus.py::fire`): when a
  `before_tool_call` plugin raises, `current` becomes a `ToolDenial` built from
  the call (`_fail_closed_denial`) instead of passing through. A broken policy
  plugin now DENIES by default rather than allowing.
- **Critical plugins are never auto-quarantined** (`bus.py::_record_failure`):
  plugins that declare `critical = True` are exempt from the 3-strike
  `_disabled` set — disabling a gating plugin would re-open the bypass. `guard`
  and `safety_gate` now set `critical = True`. They keep failing closed on every
  `before_tool_call` throw instead of going silent.

The `critical` flag is read via `getattr` on the plugin instance, so `bus.py`
(Layer 1) stays decoupled from specific plugin identities.

## Verification
- `test_before_tool_call_fails_closed_on_error` — a throwing `before_tool_call`
  returns a `ToolDenial` carrying the call's id/name.
- `test_critical_plugin_not_auto_disabled_and_keeps_denying` — 4 throws, no
  `PLUGIN_DISABLED` event, still denies on the next call.
- Full v2 unit suite: **778 passed.**

## Residual / trade-off
- A genuinely broken guard now denies **every** tool (deny-by-default) — that's
  the correct security posture (annoying beats bypassable), but it means a buggy
  guard config bricks the agent until fixed. Acceptable and intentional.
- Non-gating hooks (before_llm_call, pack_context, …) are unchanged — they still
  pass through on error, which is correct (they're not policy gates).
