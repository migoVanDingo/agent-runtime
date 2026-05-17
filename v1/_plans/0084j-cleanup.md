# 0084j — Cleanup: rename, TODO removal, CLAUDE.md update

> **Depends on:** 0084a–i all landed. This is a housekeeping phase with no new
> functionality. Run it last so it does not conflict with in-flight work on earlier phases.

## Goal

Rename `_service_checkpoint` → `_pause_check` throughout the runtime, remove all
`TODO(0083-cleanup)` markers, and update `CLAUDE.md` with the finalised contracts for
the event bus, `_pause_check`, and the threading model for both gate classes.

## Files changed

| File | Change |
|------|--------|
| `src/runtime/pipeline_context.py` | `_service_checkpoint` → `_pause_check` |
| `src/runtime/pipeline.py` | `_service_checkpoint` → `_pause_check` |
| `src/agent.py` | `_service_checkpoint` → `_pause_check` |
| `src/runtime/stages/execution.py` | `_service_checkpoint` → `_pause_check` |
| `src/runtime/stages/direct_execution.py` | `_service_checkpoint` → `_pause_check` |
| `CLAUDE.md` | New sections: Event Bus Contract, `_pause_check` contract, TUIUserGate/TUIInputGate threading, Import Discipline |

7 `TODO(0083-cleanup)` markers removed across various files (exact locations tracked by
`grep` during the cleanup pass).

## Key implementation notes

**Rename rationale:** `_service_checkpoint` was a temporary name chosen during 0083e to
avoid conflicts with an in-progress refactor. `_pause_check` is shorter and matches the
method's actual purpose: "pause or cancel check, called at stage boundaries."

**`_pause_check` contract (now in `CLAUDE.md`):**
- Field on `PipelineContext`: `_pause_check: object = None`
- Type: `Callable[[], None] | None`
- Called in `pipeline.py` at each stage transition
- May raise `TurnCancelledError` to abort the turn from within the pipeline

**CLAUDE.md additions:**

*Event Bus Contract* — documents the `stage.started` → spinner label mapping, which event
types are high-frequency (`content.token_chunk`), and the `turn.completed` drain contract.

*TUIUserGate / TUIInputGate Threading Model* — documents that both gates block the worker
thread via `threading.Event`, are unblocked from the async event loop thread via
`supply_answer()`, and must never be called from the event loop thread on the blocking
side.

*Import Discipline* — documents that `src/ui/` must never import from `runtime/`,
`agent.py`, or `tools/`. The service boundary (`src/service/`) is the only allowed
cross-layer import.

**TODO marker removal:** `TODO(0083-cleanup)` markers were temporary scaffolding notes
from 0083 phases. They are removed rather than converted to normal comments because the
underlying issues were resolved during 0084.

## Verification

```bash
cd /Users/bubz/Developer/agent/runtime/agent-runtime
# Both should produce no output
grep -rn "TODO(0083-cleanup)\|_service_checkpoint" src/ 2>/dev/null | grep -v __pycache__

python3 -m pytest tests/integration/test_service.py -q --no-header
python3 -c "from ui.app import run; from ui.conversation import ConversationModel; from ui.spinner_model import SpinnerModel; from ui.input_model import InputModel; print('OK')"
```

## Done when

- [ ] `_service_checkpoint` renamed to `_pause_check` in all 5 files
- [ ] All `TODO(0083-cleanup)` markers removed (7 locations)
- [ ] `CLAUDE.md` updated with Event Bus Contract section
- [ ] `CLAUDE.md` updated with `_pause_check` contract
- [ ] `CLAUDE.md` updated with TUIUserGate/TUIInputGate threading model documentation
- [ ] `CLAUDE.md` updated with Import Discipline section
- [ ] `grep -rn "TODO(0083-cleanup)\|_service_checkpoint" src/` returns empty
- [ ] All unit and integration tests still green
