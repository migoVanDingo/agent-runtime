# 04 — Sub-agent dispatch

How arc delegates context-heavy specialist work to scoped child agents
without blowing the main agent's context window.

## When to use a sub-agent

Use one when:

- The task requires reading a large artifact (e.g., a 12 KB Ghidra
  decompile) plus iterative reasoning over it.
- The reasoning belongs to a focused domain where a specialised system
  prompt + curated toolset gives better results than the general agent's
  prompt.
- A different provider/model would do the job better (e.g., Opus for
  reverse engineering, a coding-tuned model for code generation).
- The intermediate trace isn't useful in the parent's conversation —
  only the final summary matters.

Don't use one when:

- The task is small enough to fit in 1-2 main-agent steps.
- The result needs to *be* in the parent's conversation history (e.g.,
  the user asked a clarifying question whose answer becomes part of the
  ongoing dialogue).

## Anatomy of a sub-agent

Three pieces:

### `SubAgentSpec`

Declarative profile. Frozen dataclass. Fields:

- `name` — stable identifier, used in scope tags + tool surface
- `description` — surfaced by `arc subagent list`
- `provider` / `model` — LLM overrides (None = inherit parent's)
- `toolset_names` — which toolsets the child can use
- `skill_names` — which skills the child knows about
- `system_prompt` — specialised system prompt
- `response_format` — `"text"` (default) or `"json"`
- `response_schema` — required when `response_format="json"`
- `timeout_seconds` — wall-clock cap on the child run
- `max_iterations` — tool-loop cap inside the child

### `SubAgentTool`

A `BaseTool` adapter that wraps a `SubAgentSpec`. Tool name is
`subagent_<spec.name>`. Input schema requires a single `task: str`. The
`execute` method dispatches through `SubAgentRunner` and returns the
child's response (text, or JSON serialised if structured).

### `SubAgentRunner`

The runtime primitive. Synchronous `run(spec, task, *, parent, pause_check,
parent_turn_id) -> SubAgentResult`. Owns:

- The recursion tripwire
- Spec override merging (from `config.subagents`)
- Child Agent construction with narrowed registry / skill registry
- Scope contextvar entry
- Telemetry emission
- Wall-clock timeout enforcement
- Structured response parsing

## Lifecycle

```
parent agent receives a step with tool="subagent_X"
  │
  └─→ ToolCallExecutor.execute("subagent_X", {"task": ...})
        │
        └─→ SubAgentTool.execute
              │
              ├─→ read parent from contextvar (set by Agent.call)
              ├─→ SubAgentRunner.run(spec, task, parent=parent, …)
              │     │
              │     ├─→ check _inside_subagent tripwire → raise if True
              │     ├─→ _merge_overrides(spec) ← config.yml subagents:
              │     ├─→ emit subagent.spawned
              │     ├─→ set _inside_subagent = True
              │     ├─→ with scoped("subagent:<name>"):
              │     │     │
              │     │     ├─→ build child Agent:
              │     │     │     - narrowed ToolRegistry (no SubAgentTool!)
              │     │     │     - narrowed SkillRegistry
              │     │     │     - spec.provider or inherit
              │     │     │     - spec.system_prompt or config default
              │     │     │     - parent.user_gate (shared)
              │     │     │
              │     │     └─→ worker thread runs child.call(task)
              │     │           │
              │     │           └─→ child runs its OWN pipeline
              │     │                 - routing/planning/execution
              │     │                 - tool calls (no subagent_*!)
              │     │                 - returns final response
              │     │
              │     ├─→ parse structured response if JSON
              │     ├─→ emit subagent.completed (or subagent.failed)
              │     └─→ reset _inside_subagent
              │
              └─→ return result.text (or json.dumps(result.structured))
```

## Recursion prevention (two layers)

v1 hard-prohibits sub-sub-agents. Two independent enforcement layers:

1. **Registry filter** (`SubAgentRunner._build_narrowed_registry`):
   walks each requested toolset, filters out every `SubAgentTool`
   instance. The child's LLM is never told a `subagent_*` tool exists,
   so it can't propose one in its plan.

2. **Contextvar tripwire** (`_inside_subagent` ContextVar): True while
   any sub-agent is on the call stack. `SubAgentRunner.run` checks at
   entry and raises `SubAgentRecursionError` if re-entered. Catches
   programmatic paths that bypass the registry filter (plugins,
   regressions, future code).

Both are tested. Lifting either is a deliberate future-plan decision
(currently filed as 0094) and requires depth limits + budget propagation.

## Escalation propagation

Sub-agents share the parent's `user_gate`. When a tool inside the
child's pipeline hits `ESCALATE`:

1. `ToolCallExecutor` builds an `Escalation`.
2. The escalation's `reason` is prefixed with the current scope tag,
   e.g., `[subagent:ghidra_analyst] host execution: ghidra_analyze on 'proc'`.
3. The shared `user_gate.prompt()` shows the user the prefixed reason.
4. User answers; the answer unblocks the child's worker thread.

The user sees ONE prompt at a time. The `[subagent:…]` prefix tells
them where it came from.

## Single-active-escalation invariant

Because v1 dispatch is synchronous (parent's `worker` thread is blocked
inside the child's `worker` thread), only ONE agent (parent or child)
is mid-execution at any moment. So two escalations never compete for
the gate.

This invariant is what makes gate-sharing safe. If 0093 (async
sub-agents) ships, the gate will need a queue and this invariant goes
away.

## Failure surface

`SubAgentResult.ok = False` when:

- The child's `agent.call()` raised an exception (caught + reported)
- The wall-clock timeout fired
- `_parse_json_response` failed for a `response_format="json"` spec
  (result has `ok=True` but `structured=None`; calling skill decides
  what to do)

A False `ok` returns a `SubAgentResult` with `error` populated, NOT a
raised exception in the parent. The parent's calling tool / skill
decides whether to surface the failure to the user or replan around it.
Same handling shape as regular tool failures.

## Telemetry surface

Three event types, all with `parent_turn_id` linkage:

- `subagent.spawned` — pid, provider, model, toolset_names, skill_names,
  response_format, timeout_seconds
- `subagent.completed` — elapsed_ms, tokens_in, tokens_out, cost_usd,
  response_chars, structured (bool)
- `subagent.failed` — error_type, error_message, elapsed_ms

Plus: every event the child's pipeline emits carries
`agent_scope=subagent:<name>` so you can roll up parent and child costs
separately in pandas.

## Adding a new sub-agent

1. Define a `SubAgentSpec` in
   `src/tools/implementations/subagents/<name>.py`:

   ```python
   from runtime.subagents import SubAgentSpec, register_spec

   MY_SPEC = SubAgentSpec(
       name="code_writer",
       description="Coding-focused sub-agent.",
       toolset_names=("file_io", "shell"),
       system_prompt=_MY_SYSTEM_PROMPT,
       response_format="json",
       response_schema={...},
       timeout_seconds=600.0,
   )
   register_spec(MY_SPEC)
   ```

2. Ensure the module is imported on startup (add to
   `tools/toolsets.py:_build_subagent_toolset()` if not already
   triggered by the package's `__init__`).

3. Reference from a skill / plan: `Step(tool="subagent_code_writer",
   action_type=ActionType.SUBAGENT, description="...")`.

4. (Optional) Pin provider/model in `config.yml` under `subagents:`.

## Open issues

- **Token tracker mixing.** Child token use accumulates into parent's
  `_session_input/_output` totals via the shared tracker. Lifecycle
  events carry per-child counts so analysts can subtract; a per-call
  tracker would be cleaner.
- **Spec system_prompt override propagation.** Currently surfaced via
  `child._system_prompt_override` attribute read by `_build_pipeline`.
  Works but is more attribute-magic than ideal; could thread through
  explicit constructor args in a future refactor.
- **No retry-on-bad-JSON.** If a JSON spec's child produces malformed
  JSON, the parent gets the raw text with a warning. A schema-bounded
  retry would be more robust if telemetry shows parse failures.

## Related plans

- `_plans/0090-context-discipline-and-subagents.md` — full design.
- `_plans/0090c-implementation.md` — runner machinery as shipped.
- `_plans/0090d-implementation.md` — GhidraAnalyst as the proof case.
- `_plans/0090e-implementation.md` — provider specialisation per role.
