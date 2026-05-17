# 0062 - Runtime Refactor Phase 7: Runtime Identity Propagation

## Goal

Begin propagating active runtime identity so structured events can correlate work inside a user turn.

## Implemented

- Added `set_runtime_identity(identity)`.
- `main.py` now sets the active identity to a fresh turn identity before calling `Agent.call()`.
- Events emitted inside the turn through `get_runtime_identity()` can now inherit the active `turn_id`.

## Behavior Notes

This is still process-local identity propagation. It is enough for the CLI runtime and the current structured event sidecar, but it is not yet a fully explicit dependency passed through every stage.

## Remaining Work

- Add `identity: RuntimeIdentity` to `PipelineContext`.
- Pass identity explicitly into stages and tool execution.
- Add plan/plan-run/step-run ids during planning and execution.
- Unify artifact store and SQLModel persistence ids.

## Verification

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src
```
