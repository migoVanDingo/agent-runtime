# 0067 — Phase C: Thick event coverage

## Goal

Add emission at all non-duplicated call sites so the JSONL dataset captures a
complete causal trace of every session. Phase B wired identity — this phase
fills in the event types.

## New event types

| Event | Emitted by | Key payload fields |
|-------|------------|--------------------|
| `stage.started` | Pipeline runner | stage_name |
| `stage.finished` | Pipeline runner | stage_name, status, duration_ms |
| `llm.call.started` | BaseProvider._chat_impl wrapper | provider, model, label, n_messages, n_tools |
| `llm.call.completed` | BaseProvider._chat_impl wrapper | provider, model, label, stop_reason, input_tokens, output_tokens, latency_ms |
| `council.deliberation.started` | Council.deliberate | mode, councillor_labels, risk |
| `council.round.completed` | Council._run_round | round_number, converged |
| `council.synthesis.completed` | Council._build_result | final_verdict |
| `escalation.requested` | CLIUserGate.prompt | source, reason, tool_name |
| `escalation.resolved` | CLIUserGate.prompt | source, approved |
| `plan.created` | PlanningStage | n_steps, requires_synthesis |
| `plan.revised` | CouncilStage | n_challenges, surviving_steps |
| `step.started` | ExecutionStage | step_index, action_type, tool |
| `step.completed` | ExecutionStage | step_index, status, duration_ms, importance_score |
| `step.failed` | ExecutionStage | step_index, error_class, retry_count |
| `replan.triggered` | ExecutionStage | failed_step, reason |
| `sandbox.run` | SandboxManager | backend, isolation, exit_code, duration_ms, timed_out |

## SCHEMA.md

New `observability/SCHEMA.md` documents every event type with fields and
privacy class.

## Files touched

`runtime/pipeline.py`, `providers/base.py`, `providers/anthropic.py`,
`providers/openai_compat.py`, `runtime/council.py`, `runtime/escalation.py`,
`runtime/stages/planning.py`, `runtime/stages/council.py`,
`runtime/stages/execution.py`, `runtime/sandbox/manager.py`,
`observability/SCHEMA.md` (new).

## Exit criteria

- A recorded multi-step session JSONL has events of every type above.
- Tests verify stage.started/finished pairs emitted by pipeline runner.
- Tests verify llm.call.completed includes token counts.
