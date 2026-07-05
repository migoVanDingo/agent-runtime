# 08 — two-stage Ctrl+C during a sub-agent dispatch

**Not an audit finding** — a control bug found during live testing (2026-07-05):
while a sub-agent was running, arc was **unkillable** (Ctrl+C / Ctrl+D / ESC did
nothing) for the whole dispatch.

## The problem
A dispatch is synchronous — `SubAgentTool.execute → runner.dispatch →
child.run_turn` runs the child **on the main thread**, so the parent loop is
blocked inside the tool call. Consequences:
- **Ctrl+C** reached the SIGINT handler but it only called
  `pause_plugin.request_pause()` — which sets the *parent's* pause flag, checked
  at the parent loop's `pause_check`. The parent is blocked, so it's never seen
  until the dispatch returns.
- The **child** has its own `cancel_flag` (checked by `_ChildObserver.pause_check`)
  but it was set **only by the watchdog Timer on timeout** (`spec.timeout_s`,
  300s). No wire from Ctrl+C to it.
- **Ctrl+D / ESC** are prompt_toolkit input-mode keys — during a dispatch
  prompt_toolkit isn't reading, so they can't fire at all.

Net: trapped until the child finished naturally or hit its 300s timeout.

## The fix — two-stage Ctrl+C
- **`arc/runtime/subagents/cancel.py`** (new): a module-level holder of the
  active dispatch's `cancel_flag` (depth-1 → at most one). `cancel_active()`
  trips a not-yet-set flag and returns whether it did.
- **runner** registers its `cancel_flag` for the duration of the dispatch
  (`_cancel.register` / `unregister` in `finally`).
- **TUI SIGINT handler** (`app.py`) is now two-stage:
  - **Stage 1** — a sub-agent is running: `cancel_active()` trips its flag → the
    child raises `Cancelled` at its next iteration boundary → dispatch returns
    `status="cancelled"` (already emits `SUBAGENT_ABORTED`) → parent resumes.
  - **Stage 2** — no active sub-agent, or it's already cancelling
    (`cancel_active()` returns False): `request_pause()` bails the turn (or, with
    no pause plugin, raises `KeyboardInterrupt`).

The child-cancel path reuses the exact `cancel_flag` mechanism the watchdog
already uses, so it's the proven code path — only the *trigger* is new.

## Verification
- `test_cancel_module_two_stage_semantics` — first `cancel_active()` → True +
  flag set; second → False (escalate).
- `test_active_dispatch_is_cancellable` — a dispatch whose child tool trips the
  flag mid-run returns `status="cancelled"`.
- Full v2 unit suite: **772 passed**.

## Residual / limits
- **Not instant.** The child observes the flag at its next ReAct iteration
  boundary, so if it's mid-tool (a 60s `pip install` / `image_build`) the cancel
  lands when that tool returns — bounded by the current child tool (~up to a
  minute), vs. "trapped until timeout" before. Interrupting mid-tool (killing a
  subprocess / aborting an HTTP call) is a much larger change, deferred.
- **ESC / Ctrl+D still can't interrupt a dispatch** — they're input-mode keys;
  only Ctrl+C (a signal) is deliverable while the agent is working.
