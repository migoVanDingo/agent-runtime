# 0029 — Runtime Infrastructure Phase 7: Logging + Integration

## What

Add user/assistant message logging (outstanding request), and clean up
the deprecated planning gate.

## Changes

### Modified files

- **`src/agent.py`** — `call()` reworked:
  - Logs user message at entry:
    ```
    ── User ────────────────────────────────────────────
      analyze /bin/pwd and write a summary to results.md
    ```
  - Logs assistant response before return:
    ```
    ── Assistant ───────────────────────────────────────
      I've analyzed /bin/pwd and written the summary...
    ```
  - Refactored control flow: `_execute_plan` result is captured in a
    `response` variable instead of returning early. If planning fails at
    any stage (classifier → direct, planner → None, validator → invalid),
    falls through to `_run_loop`. The assistant response is always logged
    regardless of which path produced it.

- **`src/planning/gate.py`** — gutted to a deprecation comment. Nothing
  imports it. The `PlanningGate` class and keyword matching logic are
  removed. Safe to delete the file entirely.

## Log output example (full flow)

```
── User ───────────────────────────────────────────
  analyze /bin/pwd and write a summary to results.md
── Intent classification ──────────────────────────
  mode: plan  reason: analysis then file write — two operations
── Planning ───────────────────────────────────────
  Step 1 [analysis]: Analyze /bin/pwd using file_info, strings, and objdump
  Step 2 [file_io]: Write structured summary to results.md
── Plan validation ────────────────────────────────
  validation: VALID
── Plan ready (2 steps) ───────────────────────────
── Step 1/2 [analysis] ────────────────────────────
  Analyze /bin/pwd using file_info, strings, and objdump
  toolsets: ['analysis']
  → strings  /bin/pwd
  ← /bin/pwd: Mach-O 64-bit executable arm64
  → objdump  /bin/pwd  {'flags': '-d'}
  ← ...
── Monitor: Step 1/2 ──────────────────────────────
  monitor: heuristics PASS → auto-CONTINUE
── Step 1 complete ────────────────────────────────
── Step 2/2 [file_io] ────────────────────────────
  Write structured summary to results.md
  toolsets: ['file_io']
  → write_file  results.md  (1200 chars)
  ← OK
── Monitor: Step 2/2 ──────────────────────────────
  monitor: heuristics PASS → auto-CONTINUE
── Step 2 complete ────────────────────────────────
── Synthesizing ───────────────────────────────────
── Done ───────────────────────────────────────────
── Assistant ──────────────────────────────────────
  I've analyzed /bin/pwd and written a summary to results.md...
```

## What gets removed

- `PlanningGate` class (replaced by `IntentClassifier`)
- `PlanningGateConfig` in config.py — still present in code but no longer
  read by any component. The `planning.gate` section in config.yml is
  vestigial. Both can be cleaned up in a future commit.

## Runtime layer summary (all phases complete)

```
src/runtime/
  __init__.py
  schema.py              ← Phase 2: StepDecision, ValidationStatus, FidelityLevel, etc.
  classifier.py          ← Phase 3: IntentClassifier (LLM, replaces gate)
  validator.py           ← Phase 4: PlanValidator (code-only structural checks)
  guard.py               ← Phase 5.5: ActionGuard (pre-execution safety)
  monitor.py             ← Phase 5: ExecutionMonitor (post-execution assessment)
  context_manager.py     ← Phase 6: ContextManager (AFM-inspired packing)
  compressor.py          ← Phase 6: heuristic compression functions
  prompts.py             ← Phase 3: classifier + monitor prompts

src/providers/
  openai_compat.py       ← Phase 1: shared OpenAI SDK translation layer
  openai_provider.py     ← Phase 1: OpenAI provider
  (ollama.py refactored) ← Phase 1: now inherits from openai_compat
  (factory.py updated)   ← Phase 1: per-component provider selection
```

Execution flow through the runtime layer:

```
User message
  → [LOG] user message
  → IntentClassifier → direct | plan
  → [if plan] Planner → Plan
  → [if plan] PlanValidator → valid | retry | fall back
  → [if plan] for each step:
      → ActionGuard.check_step() → allow | block | escalate
      → ContextManager.pack() → budget-constrained messages
      → _run_step:
          → for each tool call:
              → ActionGuard.check_tool_call() → allow | block | escalate
              → tool.execute() (only if allow)
      → ExecutionMonitor.assess() → continue | retry | replan | defer | skip
  → [if plan] Synthesizer → response
  → [if direct] _run_loop (with guard + context manager)
  → [LOG] assistant response
```
