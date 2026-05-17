# 0054 - Runtime Refactoring Design: Sandboxing, Telemetry, And Core Boundaries

## Scope

This design synthesizes three inputs:

1. Claude Opus review: `_plans/0051-architecture-and-pattern-review-claude.md`
2. Codex review: `_plans/0052-source-architecture-pattern-review-codex.md`
3. New product/design constraints:
   - `bash_exec` should not run raw on the host system.
   - Existing human-readable logs are valuable and should remain easy to read.
   - Runtime data from prior runs should become an aggregate dataset for future analysis, evaluation, and multi-user usage.

The goal is not a rewrite. The goal is a refactor plan that protects the good architecture, tightens unsafe boundaries, and turns runtime behavior into analyzable data.

## Review Of Claude's Report

Claude's report is strong. I agree with its central read:

- Protect the pipeline/stage contract.
- Protect the generic council deliberation primitive.
- Extract the duplicated ReAct/tool loops.
- Add tests around pure logic first.
- Reduce singleton/global dependencies.
- Consolidate JSON parsing and structured-output contracts.
- Move prompt-injection escalation through `UserGate`.
- Split `artifact_store.py` mechanically before changing behavior.

The most useful additions in Claude's report, relative to my review:

- It correctly identifies `Council` as a reusable deliberation primitive, not just a plan critic implementation.
- It calls out the pair-atomicity in `ContextManager` as a high-value invariant worth testing.
- It emphasizes provider structured-output drift, especially Anthropic ignoring `json_schema`.
- It notes that direct mode/fallback has weaker tool authorization than planned execution.
- It frames `Plan` vs `PlanRun` as a type design problem, not only a persistence problem.

The main gaps to add:

- Shell execution is not just a bad pattern. It is a runtime isolation boundary and should be treated as a P0 safety design.
- File tools need policy enforcement too. Sandboxing bash while leaving `read_file` and `write_file` unrestricted would be incomplete.
- Logs should become a parallel structured event stream, not a replacement for the current human logs.
- Persistence should be designed for three consumers: operational memory, human debugging, and analytical datasets.
- Multi-user usage introduces identity, privacy, retention, and tenant isolation concerns that are not present in a single-user CLI.

Where I would nuance Claude's recommendations:

- Collapsing the artifact store into SQLModel may be right long-term, but it should not be the first move. The better first move is one canonical session identity plus an append-only event stream. After that, relational tables can become projections.
- Singletons are acceptable at the CLI process edge. They are a problem when runtime stages and tools pull them indirectly. The refactor should introduce a runtime container and then gradually pull dependencies inward.
- Tests should cover pure logic first, but replay/event fixtures matter just as much because agent failures are often integration failures.

## Design Principles

1. Keep the readable logs.
   The existing `_logs/*.log` format is useful. Do not turn it into noisy JSON.

2. Add structured events beside logs.
   Every important runtime action should emit a machine-readable event with stable schema, correlation ids, and privacy metadata.

3. No raw host execution by default.
   Shell commands should run through a sandbox backend with explicit mounts, environment, resource limits, and network policy.

4. Policy before execution, sandbox during execution.
   The guard decides whether something is allowed. The sandbox enforces what the process can actually touch.

5. One identity model.
   Logs, metrics, artifact memory, SQL persistence, events, plans, steps, and tool calls should share session/turn/run ids.

6. Runtime state and planner specs are different things.
   A `Plan` is an intent/spec. A `PlanRun` records execution state.

7. Refactor mechanically before changing behavior.
   Extract shared loops, stores, and interfaces first. Then harden behavior behind those interfaces.

## Target Architecture

```text
main.py
  -> RuntimeContainer
      -> Agent
          -> Pipeline
              -> stages
                  -> ToolLoopController
                      -> ToolCallExecutor
                          -> PolicyEngine
                          -> SandboxManager
                          -> ToolRegistry

EventBus side channel:
  Pipeline/stages/providers/tools/policy/sandbox/artifacts
      -> HumanLogSink       keeps current readable logs
      -> JsonlEventSink     local append-only event dataset
      -> MetricsSink        counters/cost/latency summaries
      -> ProjectionWriter   optional SQLModel tables for query UI
```

### New Core Components

#### `RuntimeContainer`

Owns process-level dependencies and passes them into `Agent`.

Responsibilities:

- Config and settings.
- Primary provider and runtime provider.
- Tool registry.
- Artifact store.
- Event bus.
- Policy engine.
- Sandbox manager.
- Persistence writer.
- User gate.

This replaces scattered module-level singleton access inside stages and tools.

Suggested file:

- `src/runtime/container.py`

#### `RuntimeIdentity`

One identity model shared everywhere.

Suggested fields:

- `session_id`: stable for a CLI session/resumable session.
- `turn_id`: one user request.
- `pipeline_run_id`: one pipeline execution for a turn.
- `plan_id`: one generated/revised plan spec.
- `plan_run_id`: one execution attempt for a plan.
- `step_run_id`: one step execution attempt.
- `tool_call_id`: one tool invocation.
- `event_id`: one event record.
- `user_id`: optional, required for multi-user mode.
- `project_id`: memory/project scope.

Suggested file:

- `src/runtime/identity.py`

#### `EventBus`

A small append-only event API used by runtime components.

Suggested files:

- `src/runtime/events/schema.py`
- `src/runtime/events/bus.py`
- `src/runtime/events/sinks.py`
- `src/runtime/events/redaction.py`

Design rule:

Human logs remain optimized for reading. Structured events are optimized for analysis.

#### `ToolCallExecutor`

One shared executor for both `ExecutionStage` and `DirectExecutionStage`.

Responsibilities:

- Tool authorization.
- Guard/policy decision.
- User approval.
- Sandbox dispatch for shell/network/eval tools.
- Tool execution.
- Result truncation.
- Prompt-injection escalation handling.
- Structured event emission.
- Structured tool result creation.

Suggested files:

- `src/runtime/tool_executor.py`
- `src/runtime/tool_loop.py`
- `src/runtime/tool_result.py`

#### `PolicyEngine`

Replaces regex-only guard decisions with a structured policy layer.

Responsibilities:

- Side-effect classification.
- Path policy.
- Network policy.
- Shell policy.
- Approval scope and cache.
- Tenant/user/project restrictions.
- Redaction before event persistence.

Suggested files:

- `src/runtime/policy/engine.py`
- `src/runtime/policy/paths.py`
- `src/runtime/policy/network.py`
- `src/runtime/policy/approvals.py`

`ActionGuard` can remain initially as one implementation inside the policy engine.

#### `SandboxManager`

Executes shell commands in a configured backend.

Suggested files:

- `src/runtime/sandbox/base.py`
- `src/runtime/sandbox/docker.py`
- `src/runtime/sandbox/host.py`
- `src/runtime/sandbox/result.py`

`host.py` exists only as an explicit dev fallback. It should be noisy when enabled.

## Bash Sandboxing Design

### Current State

`BashExecTool.execute()` calls:

```python
subprocess.run(command, shell=True, capture_output=True, text=True, timeout=config.timeouts.default)
```

That means commands run directly on the host with the runtime process privileges. `ActionGuard` regexes can block or escalate some commands, but they do not isolate the process after approval or after a missed pattern.

### Threat Model

We need protection against:

- Accidental destructive commands.
- Model-generated command mistakes.
- Prompt-injected commands.
- Path traversal outside the workspace.
- Secret exfiltration from home directory or environment.
- Network exfiltration.
- Package installs and host mutation.
- Runaway CPU/memory/process usage.
- Multi-user cross-tenant access.

### Sandbox Requirements

Default shell execution must:

- Run outside the host namespace when a container backend is available.
- Mount only approved paths.
- Mount the project workspace read-write or read-only based on policy.
- Mount `_store` only when explicitly needed.
- Hide home directories, SSH keys, cloud credentials, shell profiles, and `.env` unless explicitly allowed.
- Use an environment allowlist.
- Disable network by default.
- Enable network only after approval and only with a recorded policy decision.
- Enforce timeout, output limit, max file size, process count, CPU, and memory limits.
- Capture stdout, stderr, exit code, signal, duration, and resource usage.
- Emit structured events for command start/end and policy/sandbox decisions.

### Sandbox Backend Interface

```python
class ShellSandboxBackend(Protocol):
    def run(self, request: SandboxCommandRequest) -> SandboxCommandResult:
        ...
```

`SandboxCommandRequest`:

- `command: str`
- `cwd: str`
- `mounts: list[MountSpec]`
- `env: dict[str, str]`
- `network: NetworkPolicy`
- `limits: ResourceLimits`
- `timeout_seconds: int`
- `identity: RuntimeIdentity`

`SandboxCommandResult`:

- `exit_code: int | None`
- `stdout: str`
- `stderr: str`
- `duration_ms: int`
- `timed_out: bool`
- `resource_usage: dict`
- `sandbox_backend: str`
- `policy_decision_id: str`

### Backend Phases

#### Phase A: Host Backend With Explicit Warning

This preserves current behavior behind an interface so the rest of the runtime can be refactored.

Rules:

- Only enabled with `sandbox.backend=host`.
- Log and event records must mark `isolation="none"`.
- Multi-user mode must reject this backend.

This is a migration step, not the target.

#### Phase B: Container Backend

Use Docker or Podman where available.

Default container behavior:

- Run as non-root.
- Read-only root filesystem.
- Temporary writable `/tmp`.
- Workspace mounted at `/workspace`.
- Optional `_store` mount at `/store`.
- No host home mount.
- No inherited shell profiles.
- Network disabled by default.
- Resource limits applied.

Command execution:

```text
container_runtime run --rm
  --network none
  --cpus ...
  --memory ...
  --pids-limit ...
  --read-only
  --tmpfs /tmp
  --user nonroot
  -v <workspace>:/workspace:rw
  <image>
  /bin/bash -lc <command>
```

The host still starts the container process, but the command itself does not run raw against the host filesystem.

#### Phase C: Strong Isolation Backend

For future multi-user hosted usage, support a stronger backend:

- Firecracker/microVM.
- Remote sandbox worker.
- Ephemeral per-run container with no shared host mounts.

This should share the same `ShellSandboxBackend` interface.

### Shell Policy

`PolicyEngine` should evaluate the command before sandbox dispatch.

Decision values:

- `allow`
- `require_approval`
- `deny`

Policy input:

- Command string.
- Requested cwd.
- User id.
- Project id.
- Tool name.
- Current stage.
- Classified risk.
- Requested network.
- Requested mounts.

Important: approval does not mean unrestricted host access. Approval expands policy scope inside the sandbox.

Examples:

- `pytest`, `python -m pytest`, `rg`, `sed`, `ls`: allow in workspace sandbox.
- `curl`, `wget`, package managers: require approval and network-enabled sandbox.
- `rm -rf`, `chmod -R`, writes outside workspace: deny or require elevated approval depending on configured policy.
- Commands touching secrets paths: deny by default.

### File Tool Policy

Sandboxing bash is not sufficient. File tools must use the same path policy.

Required changes:

- Every file tool resolves paths through `PathPolicy`.
- Symlinks are resolved before policy decision.
- Reads and writes are scoped to configured roots.
- Writes outside workspace require approval or denial.
- Deletes always require approval and should record a destructive event.

Suggested `PathPolicy`:

```python
class PathPolicy:
    def classify(self, path: str, operation: Literal["read", "write", "delete"]) -> PolicyDecision:
        ...
```

## Logs As Dataset Design

### Keep Human Logs

The current logs are valuable because they are narrative and scannable. Keep them.

Do not convert `_logs/*.log` into JSON.

Do not clutter each line with full structured payloads.

Optional improvement:

- Include a short event id only in verbose/debug mode.
- Keep a sidecar event file for correlation.

### Add Structured Events

Every runtime action should emit a structured event to an append-only event stream.

Local default:

```text
_events/
  2026-05-02/
    <session_id>.jsonl
```

Each event is one JSON object per line.

### Common Event Fields

All events should include:

```json
{
  "schema_version": "1.0",
  "event_id": "EVT...",
  "event_type": "tool.call.completed",
  "ts": "2026-05-02T18:00:00.000Z",
  "session_id": "SESS...",
  "turn_id": "TURN...",
  "pipeline_run_id": "RUN...",
  "parent_event_id": "EVT...",
  "user_id": null,
  "project_id": "agent-runtime",
  "stage": "ExecutionStage",
  "privacy": {
    "classification": "internal",
    "redacted": true,
    "raw_content_stored": false
  },
  "payload": {}
}
```

### Event Types

Session and turn:

- `session.started`
- `session.resumed`
- `session.ended`
- `turn.started`
- `turn.completed`
- `turn.failed`

Pipeline:

- `pipeline.started`
- `pipeline.completed`
- `stage.started`
- `stage.completed`
- `stage.retry`
- `stage.ask_user`
- `stage.abort`
- `fallback.started`

Routing/planning:

- `routing.completed`
- `workflow.matched`
- `plan.created`
- `plan.validation.completed`
- `plan.revised`
- `plan.stripped`

Council:

- `council.started`
- `council.councillor.completed`
- `council.completed`
- `council.user_outcome`

Provider:

- `provider.request.started`
- `provider.response.completed`
- `provider.error`
- `tokens.recorded`

Tool/sandbox:

- `tool.call.started`
- `policy.decision`
- `approval.requested`
- `approval.completed`
- `sandbox.command.started`
- `sandbox.command.completed`
- `tool.call.completed`
- `tool.call.failed`

Monitor/context/artifacts:

- `monitor.assessment`
- `context.pack.completed`
- `artifact.created`
- `artifact.accessed`
- `artifact.expelled`
- `recall.completed`
- `workflow_candidate.discovered`

Security:

- `prompt_injection.scan.completed`
- `prompt_injection.approval_requested`
- `secret.redacted`
- `policy.violation`

Feedback:

- `user.feedback`
- `run.label_added`
- `evaluation.completed`

### Event Payload Examples

`tool.call.completed`:

```json
{
  "tool_call_id": "TCALL...",
  "tool_name": "bash_exec",
  "input_preview": "pytest -q",
  "input_hash": "sha256:...",
  "authorized_tools": ["bash_exec"],
  "status": "success",
  "duration_ms": 1234,
  "result_preview": "12 passed",
  "result_hash": "sha256:...",
  "result_bytes": 72
}
```

`sandbox.command.completed`:

```json
{
  "sandbox_backend": "docker",
  "isolation": "container",
  "network": "disabled",
  "mounts": [
    {"host": "<workspace>", "container": "/workspace", "mode": "rw"}
  ],
  "exit_code": 0,
  "timed_out": false,
  "duration_ms": 1234,
  "stdout_bytes": 64,
  "stderr_bytes": 0,
  "resource_usage": {
    "max_rss_mb": 128
  }
}
```

`policy.decision`:

```json
{
  "subject": "tool_call",
  "tool_name": "bash_exec",
  "decision": "require_approval",
  "reason": "network command: curl",
  "risk": "moderate",
  "approval_scope": {
    "type": "command",
    "expires_at": "2026-05-02T20:00:00Z"
  }
}
```

### Dataset Storage Strategy

#### Tier 1: Local JSONL

Write append-only JSONL per session. This is simple, robust, and easy to inspect.

#### Tier 2: SQLite/Postgres Event Table

For query UI and multi-user mode:

```text
runtime_event
  id
  schema_version
  event_type
  ts
  session_id
  turn_id
  user_id
  project_id
  stage
  payload_json
  content_hash
  privacy_classification
```

#### Tier 3: Analytics Export

Nightly or explicit export to:

- Parquet
- DuckDB
- warehouse table

Use cases:

- Tool usage analysis.
- Token/cost breakdown.
- Failure mode clustering.
- Sandbox/policy incident analysis.
- Workflow candidate mining.
- Provider quality comparison.
- Council value analysis.
- User behavior and common task patterns.
- Future imitation learning or reward modeling.

### Privacy And Multi-User Requirements

Before multiple users use the runtime, events need:

- `user_id`
- `tenant_id` or `workspace_id`
- `project_id`
- privacy classification
- redaction status
- content hashes
- raw content storage policy
- retention policy
- deletion/export support

Raw prompts, tool outputs, file contents, and URLs can contain secrets. The event stream should default to previews and hashes, with raw payloads stored only when policy permits.

Recommended redaction layer:

- Redact API keys, tokens, private keys, `.env` values.
- Redact home directory names if configured.
- Redact long file contents by default.
- Store full content as an artifact only with privacy metadata.

## Persistence And Memory Refactor

### Current Problem

There are two persistence systems:

- Artifact store SQLite for operational memory and resume.
- SQLModel DAL for sessions/plans/steps/artifacts.

They do not share one canonical session identity.

This creates correlation problems for analytics, replay, and multi-user usage.

### Target Split

Do not think in terms of "one database" yet. Think in terms of source-of-truth roles:

1. Operational memory:
   Needed by the running agent for artifacts, recall, resume, and workflow discovery.

2. Event log:
   Append-only record of what happened.

3. Query projections:
   SQLModel tables optimized for browsing sessions, plans, steps, artifacts, and users.

The event log should become the analytical source of truth. SQL tables can be maintained directly at first and later rebuilt from events.

### Required Identity Fix

All systems should use the same `session_id`.

Implementation direction:

1. Create `RuntimeIdentity` at session start.
2. Pass `session_id` into artifact store.
3. Pass the same `session_id` into SQLModel persistence.
4. Use the same id in log filenames, metrics filenames, event filenames, artifact metadata, plan rows, step rows, and recall rows.

Do this before adding more persistence features.

## Plan And Execution Type Refactor

### Current Problem

`planning.schema.Plan` and `Step` mix planner output with runtime execution state:

- `status`
- `result`
- `error`
- `retry_count`
- `deferred`
- `skipped`

### Target Types

Planner spec:

- `PlanSpec`
- `StepSpec`

Runtime execution:

- `PlanRun`
- `StepRun`
- `ToolCallRun`

This mirrors how users and analytics think:

- What was the plan?
- What happened when we ran it?
- What changed after retry/replan?

This also makes replay cleaner.

## Tool Loop Refactor

### Current Problem

`ExecutionStage` and `DirectExecutionStage` both implement tool loops. They duplicate:

- tool execution,
- guard checks,
- prompt-injection handling,
- repeated-call detection,
- result truncation,
- max token patching,
- messenger updates.

### Target Shape

```text
ToolLoopController
  owns loop state and stopping rules

ToolCallExecutor
  executes one tool call safely

ToolAuthorization
  determines whether requested tool is allowed for this mode/step

ToolResultNormalizer
  converts all results to structured status + LLM-facing text
```

Planned execution and direct execution configure the loop differently, but use the same executor.

Important change:

Direct fallback should not become a larger-permission free-for-all after a plan aborts. The fallback should inherit risk and toolset constraints from routing.

## Provider And JSON Contract Refactor

### Current Problem

There are multiple JSON parsing implementations and provider-specific structured output differences.

### Target Shape

1. Add `ProviderCapabilities`.
2. Add shared `json_extract` fallback.
3. Define schemas once per runtime decision.
4. Use native structured output where supported.
5. For Anthropic, use tool-call schema forcing for structured decisions.

Provider capability example:

```python
@dataclass
class ProviderCapabilities:
    tool_use: bool
    structured_json_schema: bool
    parallel_tool_calls: bool
    streaming: bool
    max_context_tokens: int | None
```

Consumers:

- planner,
- router,
- monitor,
- critic,
- importance scorer,
- workflow selector.

## Refactoring Phases

### Phase 0: Safety Net And Schemas

No behavior change.

Deliverables:

- Add tests for `ActionGuard`, `PlanCriticAdapter.synthesize`, `ContextManager._pack_chronological`, workflow matchers, and routing parsing.
- Add `RuntimeIdentity`.
- Add event schema dataclasses.
- Add no-op `EventBus`.
- Add `ToolResult` structured type.
- Add config section for sandbox and events.

### Phase 1: Shared Tool Executor

Behavior should remain mostly identical.

Deliverables:

- Extract `ToolCallExecutor`.
- Extract prompt-injection approval helper.
- Route all user prompts through `UserGate`.
- Use executor from both planned and direct execution.
- Emit initial structured events to JSONL.

### Phase 2: Sandboxed Bash

Deliverables:

- Add `SandboxManager`.
- Add `ShellSandboxBackend`.
- Move `bash_exec` to sandbox backend.
- Add host backend as explicit dev-only fallback.
- Add container backend.
- Add resource limits and network default-off.
- Add sandbox event payloads.

Acceptance:

- `bash_exec` no longer calls `subprocess.run(..., shell=True)` directly from the tool.
- A command cannot read `$HOME/.ssh` or `.env` unless policy explicitly permits it.
- Network command requires approval and emits policy + sandbox events.

### Phase 3: Path Policy For File Tools

Deliverables:

- Add `PathPolicy`.
- Apply to read/write/delete/move/copy/mkdir tools.
- Resolve symlinks before decisions.
- Add workspace roots and allowed external roots.
- Emit file policy events.

Acceptance:

- Writes outside workspace require approval or are denied.
- Deletes require approval.
- File tools and shell commands share policy vocabulary.

### Phase 4: Logs-As-Dataset

Deliverables:

- Event JSONL sink enabled by default.
- Human logs unchanged.
- Event export script to DuckDB/Parquet.
- Basic aggregate report script:
  - tool usage,
  - stage failure rates,
  - token cost by stage,
  - sandbox denials,
  - policy approvals,
  - monitor decisions,
  - workflow matches.

Acceptance:

- A prior session can be analyzed without parsing human logs.
- Human logs remain readable.
- Every tool call has a correlated event record.

### Phase 5: Persistence Identity Unification

Deliverables:

- One session id across artifact store, SQLModel, logs, metrics, events.
- DB schema changes if needed.
- Backfill/migration script for existing local data if worth preserving.
- Remove duplicate id generator or make it delegate to one implementation.

Acceptance:

- Session, plan, step, artifact, log, metric, and event records join on shared ids.

### Phase 6: Artifact Store Modular Split

Mechanical split first.

Suggested package:

```text
src/runtime/artifacts/
  models.py
  serialization.py
  repository.py
  file_store.py
  conversation.py
  recall.py
  decay.py
  workflow_discovery.py
  service.py
```

Acceptance:

- Public API remains compatible at first.
- Tests can instantiate temp stores without module globals.

### Phase 7: Provider Contract Cleanup

Deliverables:

- Add provider capabilities.
- Add Anthropic structured decision support.
- Replace duplicated JSON parsers with shared fallback.
- Generate planner prompt action types from schema.

Acceptance:

- Planner, monitor, critic, router, and importance decisions all use declared schemas.

### Phase 8: Replay And Evaluation

Deliverables:

- Event replay harness.
- Golden event fixtures.
- Evaluation scripts for:
  - did the run succeed,
  - what failed,
  - what policy intervened,
  - what tools were used,
  - what did the council change,
  - what should become a workflow.

Acceptance:

- A historical event file can be replayed against fake providers or mocked tools.
- Regression tests can be generated from real runs.

## Suggested Immediate Implementation Order

1. Add event schema and no-op event bus.
2. Add `RuntimeIdentity` and propagate it through logs/events/persistence in a limited way.
3. Extract `ToolCallExecutor` from duplicated execution loops.
4. Route injection prompts through `UserGate`.
5. Add `SandboxManager` and move `bash_exec` behind it.
6. Add container sandbox backend.
7. Add event JSONL sink and export script.
8. Apply `PathPolicy` to file tools.
9. Unify session ids across artifact store and SQLModel.
10. Split artifact store mechanically.

## Key Design Decisions To Make

1. Sandbox backend default:
   - Docker/Podman by default with host fallback disabled?
   - Host fallback allowed only in single-user dev?

2. Workspace write policy:
   - Is the project root read-write by default?
   - Should source files require approval to modify?

3. Network policy:
   - All network off by default?
   - Per-command approval?
   - Domain allowlists?

4. Event raw payload policy:
   - Store only previews/hashes by default?
   - Store full prompts/tool outputs locally for single-user mode?
   - Different defaults for multi-user mode?

5. Persistence target:
   - Keep operational artifact store separate for now?
   - Build SQL projections from event stream?
   - Collapse later?

6. Multi-user identity:
   - What will be the stable `user_id` and `project_id` source?

## Definition Of Done For This Refactor

The refactor is successful when:

- Human logs remain readable and useful.
- Every important runtime action has a structured event.
- Prior runs can be aggregated without parsing human log text.
- `bash_exec` runs in a sandbox by default.
- File tools enforce path policy.
- Direct fallback does not grant broader permissions than the failed planned path.
- Session ids correlate across logs, events, metrics, artifacts, plans, and steps.
- Tests cover the policy engine, sandbox request construction, tool executor, and event emission.
- Multi-user mode can be enabled without raw host execution or cross-user data leakage.

## Summary

The existing runtime has the right high-level shape. The next refactor should not flatten it. Keep the pipeline, stages, toolsets, context manager, workflow templates, and council. Refactor the unsafe and hard-to-analyze parts around them:

- shared tool execution,
- sandboxed command execution,
- path/network policy,
- structured events,
- unified identity,
- modular memory/persistence,
- stronger provider/schema contracts.

That gives the project a path from a capable single-user CLI to a safer, measurable, multi-user agent runtime without sacrificing the readable logs that already work well.
