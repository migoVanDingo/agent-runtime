# 0055 - Runtime Refactor Phase 0: Safety Net And Schemas

## Goal

Introduce the low-risk scaffolding needed for the larger runtime refactor without changing normal agent behavior.

Phase 0 establishes shared vocabulary for:

- runtime identity,
- structured events,
- structured tool results,
- sandbox/event configuration,
- tests around early safety primitives.

## Implemented

- Added `runtime.identity.RuntimeIdentity`.
- Added prefixed runtime correlation IDs with `new_id()`.
- Added `runtime.events` package:
  - `RuntimeEvent`
  - `EventPrivacy`
  - `EventBus`
  - `NoopEventSink`
  - `JsonlEventSink`
- Added `runtime.tool_result.ToolResult`.
- Added `runtime.events` and `runtime.sandbox` config sections in `config.yml`.
- Added typed config dataclasses:
  - `EventsConfig`
  - `SandboxConfig`
- Added stdlib `unittest` coverage for:
  - identity derivation,
  - event bus emission/no-op behavior,
  - structured tool results,
  - basic `ActionGuard` shell block/escalate behavior.

## Behavior Change

None intended in Phase 0 itself.

Later phases in this series now enable event JSONL output and move `bash_exec` behind `SandboxManager`; see 0057 and 0059 for those behavior changes.

## Verification

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src
```

## Next Phase

Phase 1 extracts shared tool-call execution behavior so planned execution and direct execution stop duplicating guard, injection, truncation, and result handling logic.
