# 0056 - Runtime Refactor Phase 1: Shared Tool Executor

## Goal

Start collapsing duplicated planned/direct tool-call execution behavior without changing the surrounding loop controllers.

## Implemented

- Added `runtime.tool_executor.ToolCallExecutor`.
- Added `ToolExecutionOutcome`.
- Centralized:
  - guard check,
  - guard block result,
  - guard escalation prompt,
  - approval caching,
  - missing-tool handling,
  - spinner restart around approval prompts.
- Added `runtime.injection_gate.handle_injection_warning`.
- Routed prompt-injection approval through `UserGate` instead of direct `input()` calls in runtime stages.
- Updated both:
  - `ExecutionStage`
  - `DirectExecutionStage`

## Behavior Notes

The main ReAct loops still live in their existing stage files. This phase only extracts the execution of one tool call and the injection approval helper. The later `ToolLoopController` extraction can now be smaller and lower risk.

One deliberate behavior simplification:

- On prompt-injection denial, the helper expels the quarantined artifact when an artifact key is present, instead of asking a second delete prompt. Denial means "do not keep/use this quarantined content."

## Verification

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src
```

## Next Phase

Phase 2 moves `bash_exec` behind a sandbox manager so shell commands no longer execute raw against the host by default.
