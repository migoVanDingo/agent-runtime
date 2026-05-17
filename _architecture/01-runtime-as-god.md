# 01 — Runtime as god

The foundational tenet of arc, originally drafted in `_plans/0079`. Every
later design decision is supposed to respect this.

## The rule

**The runtime owns all control-flow decisions.** Tools, skills, and
sub-agents are passive participants that execute and return data. They
do NOT decide:

- whether to retry the current step
- whether to replan
- whether to escalate to the user
- whether to abort the turn
- whether to pause / cancel

Those are runtime decisions made by:

- `ExecutionMonitor` after each step
- `ContinuationStage` after the plan completes
- `ActionGuard` before each tool call
- The pipeline itself when a stage returns RETRY / ABORT / ASK_USER
- The user, via the gates (`user_gate`, `input_gate`)

## Why this matters

Without this rule the system devolves into spaghetti:

- A tool that decides "let me retry myself" creates an infinite loop
  the runtime can't see.
- A skill that decides "let me ask the user" bypasses the gate's
  serialisation and breaks the single-active-escalation invariant.
- A sub-agent that decides to spawn its own sub-agents creates
  unbounded recursion the parent can't account for.

With this rule, the runtime has:

- **Awareness** — every meaningful decision is something the runtime
  made or witnessed, so telemetry sees it (see doc 05).
- **Control** — the runtime can pause, resume, cancel, kill anything
  it owns. Tools and sub-agents are owned things.
- **Composability** — any tool/skill/sub-agent can be slotted in or
  swapped out without altering control flow.

## Where the rule lives in code

| Concern | Owner |
|---|---|
| Retry a failed step | `ExecutionMonitor` returns `RETRY` decision |
| Replan around a failure | `ExecutionMonitor` returns `REPLAN`, pipeline runs Planner again |
| Escalate to user before tool call | `ActionGuard` returns `ESCALATE`, `ToolCallExecutor` invokes `user_gate.prompt()` |
| Ask user for clarification mid-pipeline | Stage returns `ASK_USER`; pipeline drives `input_gate.ask()` |
| Pause | `_pause_check` callable threaded through; raises `TurnCancelledError` |
| Cancel | Same path as pause, with cancel flag set |
| Sub-agent lifecycle | `SubAgentRunner.run` owns spawn/timeout/kill; emits `subagent.*` events |

## Sub-agents and the rule

Sub-agents preserve the rule when implemented correctly:

- A sub-agent runs its OWN pipeline (so within its scope, the runtime is
  still in charge — but that's the *child* runtime owning *its*
  decisions).
- A sub-agent returns a `SubAgentResult` to the parent. The PARENT
  decides whether to retry, replan, abort based on that result.
- The parent's `pause_check` propagates into the child, so cancellation
  remains a parent-owned action.
- Escalations from inside a child route through the PARENT'S user_gate
  (see doc 04). The user always sees ONE escalation source.

This is why "sub-agents shouldn't spawn sub-agents" is a hard rule in
v1: recursion would create multiple "god runtimes" with no clear chain
of command for cancellation and accounting.

## What this rule does NOT prohibit

- Tools doing complex internal work (running subprocesses, calling
  external APIs, etc.) — as long as they return data, not control-flow
  decisions.
- Skills expanding into many concrete steps — that's data driving the
  runtime's plan, not a skill bypassing it.
- Sub-agents running their own multi-step pipelines internally — again,
  internal execution is fine; what matters is the boundary.

## Counter-examples (don't do these)

```python
# WRONG — tool deciding to retry itself
class MyTool(BaseTool):
    def execute(self, tool_input):
        result = self._do_work(tool_input)
        if not result:
            return self.execute(tool_input)  # infinite recursion the runtime can't see
        return result

# RIGHT — tool returns a string; ExecutionMonitor decides RETRY
class MyTool(BaseTool):
    def execute(self, tool_input):
        try:
            return self._do_work(tool_input)
        except Exception as e:
            return f"Error: {e}"  # runtime sees this and reacts
```

```python
# WRONG — sub-agent escalating directly to a side channel
def some_subagent_code(parent_gate):
    if uncertain:
        click.confirm("...")  # bypasses the user_gate

# RIGHT — sub-agent calls a tool that hits ESCALATE; the parent's
# user_gate handles it through the standard channel
```

## Related docs

- doc 02 — pipeline ordering and stage responsibilities
- doc 04 — sub-agent dispatch semantics
- doc 05 — how the bus makes runtime decisions visible
- `_plans/0079-runtime-as-god.md` — original tenet
- `_plans/0090-context-discipline-and-subagents.md` — sub-agent design
  applying this rule
