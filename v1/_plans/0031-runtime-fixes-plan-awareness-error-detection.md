# 0031 — Runtime Fixes: Plan Awareness, Error Detection, Step Granularity

**Date**: 2026-04-15
**Status**: Implemented
**Triggered by**: Testing with OpenAI gpt-4o-mini (session SES01KP78CNM98X25PWGVYM8ZKSRN)

## Problems Observed

### 1. Multi-tool plan steps
GPT-4o-mini bundled multiple tools into a single plan step (e.g., "Analyze using file_info, strings, and objdump"). This happened because our planner prompt example demonstrated exactly that pattern. Haiku happened to break steps down further on its own, but the prompt was not enforcing it.

**Impact**: When step 1 bundles all analysis, the context manager placeholders the results before step 2 (write) can use them. The model is forced to write during the analysis step or lose the data entirely.

### 2. Context manager amnesia during plan execution
The context manager treated all large tool results as LOW importance regardless of whether they were produced by the current plan. A single objdump output (~40K tokens) would blow the 16K budget, causing all plan-produced data to be placeholdered by the next step.

**Impact**: Step 2 (write summary) had no analysis data to work with. The model either wrote during step 1 (violating step boundaries) or hallucinated content.

### 3. Model lying about failed operations
In direct execution mode, the model tried `write_file /path/to/your/document.md`, got `Error: No such file or directory`, then told the user "I have now successfully written the thorough summary." The execution monitor only runs during plan execution — direct mode had no error checking.

**Impact**: User was told operations succeeded when they actually failed.

### 4. Step results lost across transitions
When step 1 completed and step 2 began, the transition message was just "Step 1 complete. Now execute step 2: ..." — no results carried forward. Combined with context packing, step 2 had zero knowledge of what step 1 produced.

**Impact**: Write steps could not reference analysis results from prior steps.

## Fixes Implemented

### Fix 1: One tool per step (planner prompt + validator)

**Files**: `src/planning/prompts.py`, `src/runtime/validator.py`

- Updated `PLANNING_SYSTEM_PROMPT` to explicitly require one primary tool operation per step
- Removed "Prefer fewer, more meaningful steps over many small ones" (this encouraged bundling)
- Replaced the 2-step example with a 4-step example (file_info, strings, objdump, write — each separate)
- Added validator check #6: regex scans step descriptions for multiple tool names, rejects plans that bundle them

### Fix 2: Plan-aware context manager

**Files**: `src/runtime/context_manager.py`, `src/agent.py`

- `pack()` now accepts `plan_start_index` parameter
- When set, messages from that index onward (current plan execution) get importance boosted: LOW → HIGH, MEDIUM → HIGH
- `_execute_plan()` records `plan_start_index = len(messages)` before plan starts
- `_run_step()` passes `plan_start_index` through to `pack()`
- Result: analysis tool outputs survive into subsequent steps instead of being placeholdered

### Fix 3: Runtime error detection in direct mode

**Files**: `src/agent.py`

- Added `_has_error_indicator()` helper using regex for common error patterns
- In `_run_loop()`: tracks `last_had_errors` flag across iterations
- When the model ends turn after tool errors: runtime injects a correction message forcing the model to address the errors before responding to the user
- One-shot guard: `error_correction_sent` flag prevents infinite correction loops
- Logged as `runtime: model ended turn after tool errors — injecting correction`

### Fix 4: Step result forwarding

**Files**: `src/agent.py`

- Step transition messages now include the previous step's result:
  ```
  Step 1 complete. Result:
  {prev.result}

  Now execute step 2: {step.description}
  ```
- Bumped `step.result` capture from 500 to 1000 chars for richer handoff
- Even when context manager compresses raw tool output, the model's interpreted summary carries forward in the transition message

## Flow Diagram (updated)

```
User message
  │
  ├─► Intent Classifier → plan / direct
  │
  ├─► [plan] Planner
  │     │
  │     ├─► Validator (now checks multi-tool bundling)
  │     │
  │     └─► Step execution loop
  │           │
  │           ├─► ActionGuard (pre-execution)
  │           ├─► Context Manager (plan-aware boosting)
  │           ├─► Provider.chat()
  │           ├─► ActionGuard (tool-call level)
  │           ├─► Tool execution
  │           ├─► Monitor (post-execution)
  │           └─► Step transition (now carries result forward)
  │
  └─► [direct] _run_loop
        │
        ├─► Context Manager
        ├─► Provider.chat()
        ├─► ActionGuard (tool-call level)
        ├─► Tool execution
        └─► Error detection (runtime correction on end_turn)
```
