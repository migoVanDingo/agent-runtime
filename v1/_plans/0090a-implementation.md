# 0090a — Implementation notes

> Companion to `_plans/0090-context-discipline-and-subagents.md` §6 0090a.
> Records what actually shipped, deviations from the spec, and verification
> results.

## What landed

A scope contextvar + a scope-aware AFM context manager. Runtime-classifier
LLM calls now pack to a smaller budget than main-agent LLM calls, and AFM
respects the system prompt's token estimate when computing its effective
budget. End result: routing-class calls no longer exceed per-minute rate
limits on the runtime provider as conversation history grows.

## Files added

- `src/runtime/scope.py` — process-wide scope contextvar. Defines `MAIN`,
  `RUNTIME` constants, `current_scope()`, `is_subagent_scope()`,
  `scoped(name)` context manager. This module is the single source of
  truth for "which agent tier is currently executing"; later phases
  (0090b RAG-headroom scaling, 0090c sub-agent tagging, 0090c logging
  filter, 0090c agent_scope telemetry field) all read from it.
- `tests/unit/test_runtime_scope.py` — 10 tests covering scope contextvar
  behaviour (defaults, nesting, exception safety) and AFM budget
  selection (main vs runtime, system_prompt_size effect, disabled
  short-circuit, protocol compliance across all four strategies).

## Files modified

- `src/runtime/context/strategy.py` — `ContextStrategy.pack` Protocol
  gains keyword-only `system_prompt_size: int = 0`. Doc updated to
  explain the parameter and that strategies may ignore it.
- `src/runtime/context/manager.py`:
  - `_DEFAULTS` gains `runtime_message_budget_tokens: 12000`.
  - `__init__` reads `self._runtime_budget`.
  - `pack` is rewritten to:
    1. Read `current_scope()` from `runtime.scope`.
    2. Select `total_budget = self._runtime_budget if scope=="runtime" else self._budget`.
    3. Compute `effective_budget = max(1000, total_budget - system_prompt_size)`.
    4. Emit a warning when `system_prompt_size > total_budget // 2`.
    5. Include `scope`, `system_prompt_size`, `total_budget`,
       `effective_budget` in `context.pack.started`/`completed` event
       payloads so 0087 telemetry surfaces the new dimensions.
- `src/runtime/context/strategies/truncation.py` — `pack` accepts
  `system_prompt_size` (ignored).
- `src/runtime/context/strategies/sliding.py` — same.
- `src/runtime/context/strategies/rag_aug.py` — same.
- `src/runtime/stages/routing.py`:
  - Imports `scoped`, `RUNTIME`, `estimate_tokens`.
  - Wraps the system-prompt build + `context_mgr.pack` + `provider.chat`
    in `with scoped(RUNTIME):` so AFM picks the runtime budget and
    telemetry tags the call. Passes `system_prompt_size=estimate_tokens(routing_system)`
    to `pack` so the LLM call total respects the budget.
- `src/runtime/stages/skill_hint.py`:
  - Imports `scoped`, `RUNTIME`.
  - Wraps `self._selector.select(...)` call in `with scoped(RUNTIME):` —
    that's where the runtime LLM is hit (via `WorkflowSelector`).
    Contextvar propagation does the rest; `WorkflowSelector` doesn't need
    to change.
- `src/runtime/monitor.py`:
  - Imports `scoped`, `RUNTIME`.
  - Wraps the `self._provider.chat(...)` call in the LLM-judge path with
    `with scoped(RUNTIME):`.
- `src/runtime/importance.py`:
  - Imports `scoped`, `RUNTIME`.
  - Wraps `self._provider.chat(...)` in `with scoped(RUNTIME):`.
- `config.yml`:
  - `runtime.context.params.afm.runtime_message_budget_tokens: 12000`
    added with a comment pointing at this plan.

## Deviations from the spec

- **Plan §6 0090a item 3** described a `stage_provider_tier` parameter on
  `pack`. Implemented instead via the `runtime.scope` contextvar set by
  the stage entering `with scoped(RUNTIME):`. Cleaner: stages don't have
  to know about the parameter, contextvar propagates automatically into
  any nested call (e.g., `WorkflowSelector` invoked from `SkillHintStage`
  inherits the scope without code changes). Also unifies with 0090c's
  logging-filter and telemetry-tagging requirements (single source of
  truth for "which tier am I in").

- **Plan §6 0090a item 5** mentioned a log warning when system prompt
  consumes > 50% of effective budget. Implemented as a `logger.warning`
  inside `pack()`. Fires every call that triggers it (intentional —
  these are the calls likely to hit rate limits, so visibility matters).

- **Not changed yet**: `runtime/stages/_execution_stage.py`,
  `runtime/stages/execution/step_loop.py`, `runtime/stages/planning.py`,
  `runtime/stages/council.py`, `runtime/stages/continuation.py`, and the
  `ToolLoop` pack call sites all currently call `pack()` without
  `system_prompt_size`. They use the main provider and the larger
  budget, so the existing behavior is preserved (no breakage). When
  0090b lands they'll start passing `system_prompt_size` too — that's
  where the system-prompt audit happens.

## Verification

- Compile-check: clean (`python -m compileall -q src/`).
- Full pytest: 146 passed / 9 failed (same 9 pre-existing 0085-refactor
  failures). Zero new regressions.
- New tests: 10 in `tests/unit/test_runtime_scope.py`, all pass.
- Smoke test (interactive):
  - Main scope with 25k of messages + 50k budget → all kept.
  - Runtime scope with 25k of messages + 5k runtime budget → packing
    engages, total tokens drop to fit.
  - `system_prompt_size=15000` against 20k budget → effective budget
    5000, packing engages, warning fires.
  - Nested `with scoped("a"): with scoped("b"): ...` correctly restores
    outer scope on exit, including under exception.

## What changes user-visible behavior

- A user running with the default config will see a warning in
  `session.log` whenever a runtime-stage system prompt exceeds 6000
  tokens (50% of the 12000 runtime budget). This is the new diagnostic
  for "your routing call is about to be huge."
- `context.pack.started`/`completed` events on the runtime bus gain
  four new payload fields (`scope`, `system_prompt_size`, `total_budget`,
  `effective_budget`). Old analysts that only read the previous fields
  still work; new ones can group by `scope` to compare runtime vs main
  context costs.
- The 119k routing call scenario from session
  `SES01KRRZQY3GPYX8D3WCMD54936K` is bounded under the new defaults:
  message packing tops out at 12000 tokens for runtime scope (less
  system prompt size), instead of 65536. Whether that fully prevents a
  429 depends on the system-prompt size — that's what 0090b will cap.

## What hasn't shipped yet (defers to later 0090 phases)

- The logging filter that prefixes log records with the scope tag —
  **0090c**.
- TUI spinner showing active scope — **0090c**.
- `agent_scope` field on runtime events — **0090c**.
- System prompt size capping (analysis manifest, tool schema narrowing,
  RAG block scaling) — **0090b**.

## Open issues / known limitations

- The `runtime_message_budget_tokens: 12000` default is a guess based on
  Anthropic Haiku's 50k/min tier. Users on other providers / tiers may
  need to tune it. Documented in `config.yml` with a comment pointing at
  the plan.
- AFM's `effective_budget` has a floor of 1000 tokens so callers with
  absurdly large system prompts still get *something* to pack against.
  An LLM call that small is probably broken anyway; the floor exists to
  avoid `effective_budget <= 0` math errors downstream.
- Stages that hit the main provider don't yet pass `system_prompt_size`.
  That's fine for backwards compat (default 0 = old behavior) but the
  total LLM call size isn't bounded for main-provider stages either —
  the fix is 0090b, which adds the system-prompt audit.
