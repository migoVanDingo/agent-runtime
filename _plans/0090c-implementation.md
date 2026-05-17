# 0090c — Implementation notes

> Companion to `_plans/0090-context-discipline-and-subagents.md` §6 0090c.

## What landed

A complete sub-agent dispatch primitive: spec/result/registry/runner, the
recursion tripwire, the scope-aware logging filter, the `agent_scope`
telemetry field, and the `SubAgentTool` adapter that exposes any spec to
the agent as a regular tool. No concrete sub-agent yet — that's 0090d.

## Files added

### New package: `src/runtime/subagents/`

- `__init__.py` — public-API exports.
- `spec.py` — `SubAgentSpec` (frozen dataclass, validates response_format/
  schema combos in `__post_init__`), `SubAgentResult`, exception hierarchy
  (`SubAgentError`, `SubAgentTimeoutError`, `SubAgentRecursionError`).
- `registry.py` — process-level spec registry. `register_spec`, `get_spec`,
  `known_specs`, `all_specs`, `clear_for_tests`.
- `runner.py` — `SubAgentRunner.run(spec, task, *, parent, pause_check,
  parent_turn_id)`. Owns the recursion tripwire (contextvar
  `_inside_subagent`), child Agent construction, scope contextvar entry,
  lifecycle telemetry emission, wall-clock timeout enforcement (worker
  thread + join), and structured-response parsing for JSON specs.
- `context.py` — contextvars that thread parent-agent state into tool
  dispatch: `current_parent_agent`, `current_pause_check`,
  `current_parent_turn_id`, and `parent_context(agent=, pause_check=,
  turn_id=)` context manager that Agent.call wraps the pipeline in.

### New package: `src/tools/implementations/subagents/`

- `__init__.py` — package docstring explaining why sub-agent tools live in
  their own subpackage (so the registry can filter them out by class for
  recursion prevention).
- `tool.py` — `SubAgentTool(spec)` adapter. Tool name is
  `subagent_<spec.name>`. `execute(tool_input)` reads the parent from
  contextvars, runs the spec via `SubAgentRunner`, returns text or
  serialized JSON. Returns an error string if no parent context is active
  (defensive — shouldn't happen in normal flow).

### Logging

- `src/logger.py` — new `_ScopeTagFilter` class. Prefixes each log
  record's message with `[runtime]` or `[subagent:<name>]` based on
  `runtime.scope.current_scope()`. Empty/main scope produces no prefix
  (default behavior unchanged). Filter is attached to both the file
  handler and the optional verbose-stdout handler.

### Telemetry

- `src/runtime/events/schema.py` — `RuntimeEvent` gains
  `agent_scope: str | None = None`. Added to the flat-fields write list
  in `to_dict()` so it appears as a top-level column in `runtime.jsonl`.
- `src/runtime/events/bus.py` — `EventBus.emit()` auto-populates
  `agent_scope` from `runtime.scope.current_scope()` when the field is
  None. Call sites can override explicitly (e.g., the runner's
  `subagent.spawned` event tags itself with the parent's scope, not the
  child's, for clean parent/child linkage).

### Agent wiring

- `src/agent.py:Agent.call()` — wraps `self._pipeline.run(context)` in
  `with parent_context(agent=self, pause_check=checkpoint_fn,
  turn_id=db_session_id):` so `SubAgentTool.execute()` can find the
  parent agent + propagate the pause check.

## Files added (tests)

- `tests/unit/test_subagent_runner.py` — 15 tests covering spec validation,
  registry semantics, the recursion tripwire, scope-tag stamping on
  events, contextvar threading via `parent_context`, the SubAgentTool
  name prefix, the input schema requirement, and — critically — the
  recursion-prevention layer 1 invariant that the narrowed registry
  drops `SubAgentTool` instances.

## Recursion prevention (two layers as planned)

1. **Registry filter** — `SubAgentRunner._build_narrowed_registry` walks
   each requested toolset and filters out instances of `SubAgentTool`
   regardless of toolset membership. The child's LLM never sees a
   `subagent_*` tool in its tool list.

2. **Contextvar tripwire** — `_inside_subagent: ContextVar[bool] = False`.
   Set to True on `SubAgentRunner.run` entry, reset on exit. Any
   re-entry raises `SubAgentRecursionError` immediately. Catches any
   programmatic path that bypasses the registry filter (plugins,
   regressions, future code).

Both are unit-tested.

## Escalation propagation

The child agent is constructed with `user_gate=parent.user_gate`. Any
tool that hits ESCALATE inside the child's pipeline goes through
`ToolCallExecutor → user_gate.prompt(escalation)`, which is the same
gate the parent uses. The user sees the prompt via the TUI.

What's not yet implemented in 0090c: the `[subagent:<name>]` prefix on
the escalation prompt itself. The plan's spec calls for prefixing the
displayed reason with the sub-agent scope so the user knows where the
prompt came from. Easiest place to do this is in
`ToolCallExecutor.execute()` when building the escalation — read
`runtime.scope.current_scope()` and prepend it to the reason string.
Punted to a follow-up touch-up; everything else from the escalation
design is in place and verified by `tests/unit/test_subagent_runner.py::
test_subagent_scope_active_during_run` (the scope IS correctly set
during child execution).

## Single-active-escalation invariant

Because `SubAgentRunner.run` is synchronous (it blocks the parent's
worker thread until the child returns), only one agent is ever
mid-execution at a time. There's no possibility of competing
escalations against the shared gate. This is a precondition for the
gate-sharing approach. Documented in the spec dataclass docstring and
the runner module docstring; if 0093 adds async sub-agents, the gate
will need a queue and this invariant goes away.

## Deviations from the plan

- **`SubAgentRunner.run` parent argument is positional-keyword, not just
  positional.** Plan implied positional; signature is `run(spec, task, *,
  parent, pause_check, parent_turn_id)`. Clearer at call sites.

- **No `inherit_history` flag yet** (plan §9 Q1). Default behavior: child
  starts with an empty messenger. The flag isn't needed for any v1
  sub-agent and adding it would complicate the runner; punted.

- **Spec system_prompt override.** The plan called for the child to use
  `spec.system_prompt`. Implemented as `child._system_prompt_override`
  attribute on the child agent. **Currently no stage in the pipeline
  reads this attribute** — stages still pull from `config.agent.system_prompt`.
  Adding "stages prefer the override when present" is a follow-up that
  belongs to 0090d (the first concrete sub-agent will need it to work).
  Flagged in this doc so it's visible.

- **Cost tracking via `token_tracker._session_input/_output` deltas.** The
  token tracker accumulates per-session totals; we snapshot before and
  after the child run. This works but bleeds child token use into the
  parent's session totals. Acceptable for v1 because the lifecycle
  events emit per-child token counts as their own fields (analysts can
  subtract). Better long-term: a per-call tracker. Punted.

## Verification

- Compile-check: clean.
- 15 new unit tests pass.
- Full pytest: 175 passed (+29 from 0090a/b/c combined), 9 pre-existing
  failures, no new regressions.
- Smoke test (interactive):
  - Logger output for `[main]` (no tag), `[runtime]`, `[subagent:demo]`
    scopes — visually distinct ✓.
  - `agent_scope` field auto-populated on every `RuntimeEvent.emit`
    call ✓.
  - Recursion tripwire raises `SubAgentRecursionError` when a runner
    is invoked from inside another runner ✓.
  - `SubAgentSpec(response_format="json")` without a schema raises in
    `__post_init__` ✓.
  - `SubAgentRunner._build_narrowed_registry` drops `SubAgentTool`
    instances from the child's registry ✓.

## What changes user-visible behavior

- `session.log` lines now show `[runtime]` / `[subagent:<name>]` prefixes
  where applicable. The most common visible effect: routing/skill_hint/
  monitor/importance logs get `[runtime]` prefixed.
- `runtime.jsonl` events gain an `agent_scope` top-level column.
- No sub-agent dispatch happens yet — 0090d wires the first one in.

## What hasn't shipped yet (defers to later phases)

- A concrete sub-agent (`GhidraAnalyst`) — **0090d**.
- Deep-disassembly skill calls the sub-agent via `subagent_ghidra_analyst`
  step — **0090d**.
- Stages preferring `child._system_prompt_override` when set — **0090d**
  (needed to make the sub-agent's specialised prompt take effect).
- Per-spec provider/model config overrides via `config.yml` — **0090e**.
- `arc subagent list` CLI — **0090e**.
- TUI spinner showing active scope — **0090e** (small touch).

## Open issues / known limitations

- **Sub-agent token cost mixes into parent's session_input/_output**
  tracker. Acceptable trade-off for v1 (lifecycle events carry per-child
  counts). A future per-call tracker would cleanly separate.
- **System prompt override isn't read by stages yet.** Child agents
  currently inherit `config.agent.system_prompt`. 0090d's deep-disassembly
  wiring will surface this; fix lands there or in a small touch-up patch.
- **Timeout uses Python thread join + abandonment.** The orphaned thread
  finishes its work after the runner returns. Worst case: extra tokens
  consumed against the provider before the LLM call completes naturally.
  Subprocess isolation would let us hard-kill — that's the model used for
  Ghidra and could be applied here if profiling shows it matters.
- **Escalation prompt doesn't yet carry the `[subagent:<name>]` prefix.**
  Tracked in this doc; fix is a one-line read of `current_scope()` in
  `ToolCallExecutor`.
