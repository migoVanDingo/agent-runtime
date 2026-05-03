# Source Architecture And Pattern Review

## Scope

This review intentionally avoids reading any documents inside `_plans`. The only `_plans` access used was a filename listing to follow the numbering convention. The analysis is based on source code, root configuration, packaging metadata, and top-level project docs outside `_plans`.

Key source areas inspected:

- CLI/session orchestration: `src/main.py`, `src/agent.py`
- Runtime pipeline: `src/runtime/pipeline.py`, `src/runtime/stages/*`
- Planning/workflows: `src/planning/*`, `src/workflows/*`
- Providers: `src/providers/*`
- Tool layer: `src/tools/*`, representative tool implementations
- Memory/persistence: `src/runtime/artifact_store.py`, `src/runtime/persistence.py`, `src/db/*`
- Configuration/observability: `config.yml`, `src/config.py`, `src/settings.py`, `src/logger.py`, `src/runtime/token_tracker.py`, `src/runtime/council_metrics.py`

## Executive Summary

This project is an agent runtime CLI with a meaningful architecture already in place: provider abstraction, dynamic toolsets, staged routing/planning/execution, workflow templates, context compression, artifact memory, safety gates, execution monitoring, and adversarial plan review. The strongest pattern is the `Pipeline` stage contract: it gives the runtime a place to put new controls without burying everything in `Agent`.

The biggest risks are not conceptual. They are implementation boundaries. The runtime currently has duplicated persistence systems, large god modules, weak dependency/package hygiene, regex-based security around powerful primitives, and multiple prompt/JSON contracts that drift from each other. These are manageable, but they should be handled before the runtime grows much more.

High-priority improvements:

1. Unify session identity and persistence between `ArtifactStore` and the SQLModel DAL.
2. Split `artifact_store.py`, `execution.py`, and `toolsets.py` into smaller owned components.
3. Replace regex-only shell/filesystem safety with a capability policy layer and path sandbox.
4. Make provider capabilities explicit, especially structured output/tool-call support.
5. Add a real test suite for stage transitions, planners, guards, provider translation, and artifact persistence.
6. Clean packaging: dependencies, generated metadata, tracked SQLite DBs, and source package layout.

## Architecture

### Runtime Shape

The application is a synchronous Python CLI. `src/main.py` owns process startup, argument parsing, session resume selection, artifact store initialization, logging, and the input loop. Each user turn calls `Agent.call()`.

`Agent` is a dependency assembler. It builds:

- primary and runtime LLM providers,
- `Messenger`,
- `ToolRegistry`,
- `StaticRouter`,
- `ContextManager`,
- workflow selector/matcher,
- planner/synthesizer,
- validator/critic/council,
- guard/escalation gate,
- execution monitor,
- importance scorer,
- ordered `Pipeline`.

The pipeline is the runtime spine:

1. `RoutingStage`
2. `DirectInlineStage`
3. `WorkflowMatchStage`
4. `PlanningStage`
5. `EntityCriticStage`
6. `ValidatorStage`
7. `CouncilStage`
8. `ExecutionStage`
9. `SynthesizerStage`
10. `DirectExecutionStage`

`DirectExecutionStage` is also the fallback when a pipeline stage returns `ABORT`.

### Provider Layer

`BaseProvider.chat()` normalizes providers to:

- `ProviderResponse`
- `TextBlock`
- `ToolUseBlock`
- `TokenUsage`

Anthropic has a native provider. OpenAI, Ollama, Grok, DeepSeek, and Gemini share `OpenAICompatibleProvider`, which translates Anthropic-style messages/tools into OpenAI function-call format.

This is a strong boundary, but it is under-specified. Providers differ in structured output, tool-call semantics, JSON schema support, streaming, rate-limit behavior, and context limits. Today those differences are mostly hidden behind a single `chat()` signature.

### Tool Layer

Tools implement `BaseTool` with:

- `name`
- `description`
- `weight`
- `input_schema`
- `execute()`
- `safe_execute()`

`Toolset` groups tools, routing rules, and planner guidance. `ToolRegistry` registers all toolsets, provides tool schemas, and exposes tool metadata to routing/planning/critic components.

Toolsets include file I/O, shell, binary analysis, crypto, web, data, artifact memory, search, git, document, and briefbot corpus tools.

### Planning And Workflow Layer

There are two planning paths:

- Workflow templates generate deterministic `Plan` objects for known task shapes.
- `Planner` asks an LLM for a JSON plan and parses it into `planning.schema.Plan`.

Plans use structured dataclasses:

- `Plan`
- `Step`
- `StepFlags`
- `ActionType`
- `StepStatus`

`PlanValidator` performs code-level validation before execution: step count, numbering, action type registration, description presence, tool existence, and write-output completeness.

### Runtime Controls

The runtime includes several control loops:

- `ContextManager` packs long conversations using similarity, recency, importance, and compression.
- `EntityCritic` corrects likely hallucinated path references before execution.
- `PlanCritic` uses `Council` to review plans before execution.
- `ExecutionMonitor` assesses step results and can continue, retry, replan, defer, skip, or escalate.
- `ActionGuard` checks shell, file, network, and eval-like operations before execution.
- `WebInspector` scans fetched content for prompt injection before the content is used.

### Persistence And Memory

There are two persistence systems:

- `runtime.artifact_store.ArtifactStore`: a synchronous SQLite-backed store for artifacts, conversation history, resumable sessions, decay, request clustering, workflow candidates, and semantic recall.
- SQLModel DAL under `src/db`: async database models for agent sessions, plans, steps, artifacts, and read-only Briefbot models. `runtime.persistence.PersistenceWriter` bridges sync runtime code to the async DAL.

This split is currently the riskiest architecture boundary in the codebase.

## Dataflows

### Startup And Session Flow

1. `main()` loads config from `config.yml` and settings from environment.
2. If artifact memory is enabled, `init_store()` opens `_store/artifacts.db`.
3. A new or resumed artifact-store session is selected.
4. Logging and council metrics are initialized using the artifact-store session id.
5. Previous conversation messages may be loaded from `ArtifactStore`.
6. `Agent` is constructed and pre-warms the embedding model.

### User Turn Flow

1. CLI reads user input.
2. `Agent.call()` logs the user message.
3. Startup recall may inject related prior sessions/artifacts into the effective message.
4. The original user message is appended to `Messenger`.
5. Optional SQLModel persistence starts a DB session.
6. A `PipelineContext` is created and passed through the pipeline.
7. Final response is logged, stored on `Agent.last_response`, and printed by the CLI.
8. Artifact-store request logging records the request for workflow discovery.

### Routing Flow

1. `ContextManager.pack()` produces a budgeted conversation view.
2. `RoutingStage` calls the provider with routing header instructions.
3. `parse_routing_response()` extracts `<route>{...}</route>`.
4. If mode is `direct` and answer text is clean, `DirectInlineStage` returns `DONE`.
5. Otherwise direct requests go to `DirectExecutionStage`; plan requests continue.

### Planned Execution Flow

1. `WorkflowMatchStage` tries classifier hint, regex workflow match, then LLM workflow selector.
2. If no workflow matches, `PlanningStage` calls the planner and validates the result.
3. `EntityCriticStage` checks plan entity references against context.
4. `ValidatorStage` logs the plan and aborts if none exists.
5. `CouncilStage` may challenge and revise or strip plan steps.
6. `ExecutionStage` executes steps sequentially.
7. Each step selects exactly one declared tool, plus limited utility tools.
8. `ActionGuard` checks step/tool risk.
9. Tool results go back into `Messenger`.
10. `ImportanceScorer` marks result importance for future context packing.
11. `ExecutionMonitor` decides whether to continue, retry, replan, defer, skip, or escalate.
12. `SynthesizerStage` creates the final answer when `requires_synthesis=true`.

### Direct Execution Flow

1. `DirectExecutionStage` runs a free-form ReAct-style tool loop.
2. `StaticRouter` selects toolsets using heuristic rules and embeddings.
3. The provider can call any exposed tool in the selected toolsets.
4. The guard checks each tool call.
5. Loop controls enforce max iterations, max tool calls, repeated-call detection, error correction prompts, and forced wrap-up.

### Artifact And Recall Flow

1. Tools store intermediate values with `ArtifactStore.set()`.
2. Small values are inline SQLite rows; larger values go to `_store/data`.
3. Conversation history is flushed at session end.
4. Session summaries and artifact summaries may be embedded.
5. Future sessions recall related summaries/artifacts by cosine similarity and project tags.
6. Request embeddings are clustered to suggest recurring workflow candidates.

## The Good Patterns

### 1. Stage Pipeline With Explicit Transition Semantics

What is good:

`Pipeline`, `Stage`, `StageResult`, `StageStatus`, and `PipelineContext` form the cleanest architectural boundary in the project. Stages return `OK`, `DONE`, `RETRY`, `ASK_USER`, or `ABORT`, and the runner owns transition behavior.

Why it matters:

This gives runtime controls a shared protocol. Routing, planning, entity correction, validation, council review, execution, and synthesis can evolve independently.

How to extend:

- Add new stages for policy, budget estimation, execution dry-run, plan diffing, or human approval.
- Add stage-level metrics and tracing without changing business logic.
- Add stage contract tests that simulate transitions without LLM calls.
- Make stage IO more explicit by adding typed context snapshots or per-stage declared read/write fields.

### 2. Provider Normalization

What is good:

Providers are normalized into common block types and token usage. OpenAI-compatible providers reuse one translation layer.

Why it matters:

The runtime can swap primary and runtime providers without changing stage code.

How to extend:

- Add `ProviderCapabilities`: supports tools, supports JSON schema, supports parallel tool calls, max context, max output, streaming, native safety metadata.
- Add provider conformance tests using fake responses.
- Move retry/backoff and token tracking into a shared provider wrapper instead of duplicating inside providers.

### 3. Toolsets As Routing And Planning Units

What is good:

Toolsets bundle tools, routing rules, descriptions, and planner notes. That creates a natural domain boundary.

Why it matters:

Adding a domain like `document` or `git` does not require editing the planner prompt manually in multiple places.

How to extend:

- Add tool metadata for side effects: read/write/delete/network/eval/shell.
- Add required permissions and allowed path roots per tool.
- Generate guard policy, planner prompt, and docs from the same metadata.
- Add tool contract tests: schema validation, representative success, representative failure, output size behavior.

### 4. Structured Plans Plus Code-Level Validation

What is good:

Plans are dataclasses with explicit action types, tool names, statuses, flags, and serialization. `PlanValidator` catches structural errors before execution.

Why it matters:

It reduces the blast radius of malformed LLM planner output.

How to extend:

- Convert `Plan`, `Step`, and `StepFlags` to Pydantic models or SQLModel-compatible value objects for stronger validation.
- Enforce tool/action-type consistency from registry metadata.
- Add plan schema versioning.
- Add semantic validation for data dependencies: produced artifact keys, consumed artifact keys, write paths, and step ordering.

### 5. Runtime Defense In Depth

What is good:

The project does not rely on one safety control. It has guard checks, monitor checks, entity correction, context packing, council review, prompt injection scanning, and fallback paths.

Why it matters:

Agent failures tend to be compound. Multiple simple controls catch more issues than one heavyweight control.

How to extend:

- Make guard decisions structured and auditable.
- Feed monitor failures into future workflow generation.
- Use council results as evaluation data.
- Add a policy simulation mode that shows what would run before anything executes.

### 6. Artifact Memory And Workflow Discovery

What is good:

The artifact store goes beyond key-value memory. It handles resumable sessions, conversation persistence, decay, semantic recall, project scoping, request logging, and workflow candidate discovery.

Why it matters:

This is the foundation for making the runtime improve from repeated use.

How to extend:

- Promote approved workflow candidates into generated workflow templates.
- Add artifact lineage: which tool call created which artifact from which input.
- Add typed artifact schemas and consumers.
- Add project-level memory dashboards or CLI inspection commands.

### 7. Observability Hooks

What is good:

Session logs, token tracker labels, council metrics JSONL, and log analysis scripts make behavior inspectable.

Why it matters:

Agent runtime quality depends on replaying and understanding failures.

How to extend:

- Emit one structured event stream for stages, tool calls, guard decisions, monitor decisions, and provider calls.
- Correlate all events with one session id and turn id.
- Use metrics records as regression fixtures.

## The Bad Patterns

### 1. Dependency And Packaging Drift

Best practice:

A Python project should have one authoritative dependency declaration, a lock or constraints file for repeatable installs, and no committed generated packaging metadata or runtime databases.

Current gap:

The code imports `openai`, `yaml`, `sentence_transformers`, and `sklearn`, but `requirements.txt` does not list `openai`, `PyYAML`, `sentence-transformers`, or `scikit-learn`. `pyproject.toml` declares no runtime dependencies. `src/arc.egg-info` is committed. `data/agent.db` and `src/data/agent.db` are tracked source files.

Improvement path:

1. Move dependencies into `pyproject.toml` under `[project.dependencies]`.
2. Generate a lock file with the chosen toolchain.
3. Add missing dependencies explicitly.
4. Remove generated `src/arc.egg-info` from git.
5. Remove tracked SQLite DBs and add runtime DB paths to `.gitignore`.
6. Add a clean install smoke test that imports `main`, builds config, and instantiates a fake-provider `Agent`.

### 2. Configuration Is Split Across Dataclasses, Pydantic Settings, And YAML

Best practice:

Configuration should be validated at startup with clear errors, typed defaults, and one model hierarchy.

Current gap:

`config.yml` is manually mapped into dataclasses in `src/config.py`; environment settings use Pydantic in `src/settings.py`; some defaults live in YAML, some in dataclass defaults, some in settings defaults. Missing or malformed YAML keys can fail late or opaquely.

Improvement path:

1. Use Pydantic models for both YAML config and environment settings.
2. Validate config once at startup and fail fast.
3. Add config tests for defaults, missing keys, invalid enum values, and provider selection.
4. Add a redacted config dump in verbose mode for debugging.

### 3. Prompt And Schema Contracts Drift

Best practice:

LLM output contracts should be generated from one schema or validated against one canonical schema.

Current gap:

`PLAN_JSON_SCHEMA` includes action types such as `search`, `git`, `document`, and `briefbot`, while `PLANNING_USER_TURN` shows a narrower placeholder list that omits some of them. Examples include `briefbot`, so the prompt contradicts itself. Anthropic receives the `json_schema` argument but the provider ignores it, so structured output enforcement is inconsistent by provider.

Improvement path:

1. Generate prompt action-type lists from `ActionType`.
2. Generate JSON schema from Pydantic models rather than hand-maintaining it.
3. Add provider capability checks for structured output.
4. For providers without native schema support, validate and repair with a deterministic retry path.

### 4. Error Handling Often Swallows Operational Failures

Best practice:

Catch narrow exceptions, log structured details, and return typed error states when callers need to react.

Current gap:

There are many broad `except Exception` blocks. Some are appropriate around optional subsystems, but others hide persistence failures, workflow generation failures, store failures, and artifact cleanup failures. Tool errors are string-matched later by monitor heuristics.

Improvement path:

1. Introduce typed runtime errors for provider, tool, persistence, validation, and policy failures.
2. Return structured tool results with `ok`, `error_code`, `message`, and `data`.
3. Keep human-readable strings for LLM context, but make runtime decisions from structured status.
4. Audit broad exception handlers and classify each as optional, recoverable, or bug.

### 5. Async DAL Is Bridged By Creating A New Event Loop Per Call

Best practice:

A system should have a clear concurrency model. If the runtime is sync, keep persistence sync; if persistence is async, run one async boundary and reuse session factories.

Current gap:

`PersistenceWriter` calls `run_async()`, which creates a fresh event loop for each DB operation. This works for a simple CLI but is inefficient and awkward if the runtime later becomes a service, runs nested async contexts, or increases write volume.

Improvement path:

1. Decide whether the runtime is sync or async.
2. For sync CLI, consider a sync SQLAlchemy engine for runtime-owned DB writes.
3. For async future, make the CLI run an async turn loop and pass an async unit-of-work through stages.
4. Batch step writes or emit events to a persistence worker.

### 6. No Real Test Suite

Best practice:

Agent runtimes need tests at contract boundaries because live LLM behavior is non-deterministic.

Current gap:

There are no conventional `tests/` or `test_*.py` files. The only `_tests` files are sample binary fixtures.

Improvement path:

1. Add fake providers with scripted responses.
2. Test pipeline transitions for every `StageStatus`.
3. Test provider translation for Anthropic-style and OpenAI-style tool calls.
4. Test guard decisions with safe, escalate, and block cases.
5. Test planner parsing and validation using fixed JSON fixtures.
6. Test artifact store persistence/recall with temp SQLite databases.

## The Ugly Patterns

### 1. Two Persistence Systems Use Different Session Identities

Best practice:

A user turn/session should have one canonical session id. All logs, artifacts, plans, steps, metrics, and summaries should correlate through that id or explicit child ids.

Current gap:

`main.py` creates an artifact-store session id using `utils.generate_id("session")`, which produces `SES...`. `PersistenceWriter.start_session()` creates a separate SQLModel `AgentSession` id using `db.utils.generate_id(IdPrefix.SESSION)`, which produces `SESS...`. `ExecutionStage` records plans and steps under the SQLModel session id. `ArtifactStore.set()` calls `PersistenceWriter.record_artifact()` using the artifact-store session id instead. That means artifact rows can point at a session id that does not exist in the SQLModel `agent_session` table.

Impact:

- Cross-store correlation is unreliable.
- Postgres or strict foreign key enforcement can reject artifact rows.
- Metrics/log/session/artifact views cannot be joined cleanly.
- Resume semantics and DB session semantics are not the same thing.

Improvement path:

1. Define one `SessionIdentity` created at CLI/session start.
2. Pass that id into both `ArtifactStore.init_session()` and `PersistenceWriter.start_session()`, or have one system own session creation and the other reference it.
3. Remove the duplicate `src/utils.generate_id()` or align it with `db.utils.generate_id()`.
4. Add a migration/backfill plan if existing persisted data matters.
5. Add an integration test proving one user turn creates joinable session, plan, step, artifact, metric, and conversation records.

### 2. `ArtifactStore` Is A God Object

Best practice:

Storage, conversation history, recall indexing, decay, workflow discovery, session lifecycle, and artifact CRUD should be separate services behind small interfaces.

Current gap:

`src/runtime/artifact_store.py` is 1,278 lines and owns schema DDL, SQLite connection lifecycle, sessions, conversations, CRUD, file-backed storage, tags, decay, embeddings, recall, request logging, workflow discovery, and singleton access.

Impact:

- Hard to test in isolation.
- Hard to migrate schema safely.
- Hard to reason about transactional boundaries.
- New memory features will keep increasing coupling.

Improvement path:

1. Split into `ArtifactRepository`, `ConversationRepository`, `SessionRepository`, `RecallIndex`, `WorkflowDiscoveryService`, and `ArtifactService`.
2. Move DDL to migrations or schema modules.
3. Replace module-level singleton with explicit dependency injection.
4. Use temp DB fixtures to test each repository.

### 3. Artifact Keys Are Global Primary Keys

Best practice:

Artifacts need durable ids and scoped aliases. A key like `paper_content` should be unique only within a session/project namespace unless intentionally global.

Current gap:

The artifact SQLite table uses `key TEXT PRIMARY KEY`. That makes artifact keys global across all sessions. A later session storing the same key can overwrite the earlier artifact metadata/value.

Impact:

- Session memory can be corrupted by common artifact names.
- Recall can return surprising data.
- Resume behavior becomes ambiguous.
- Workflow-generated artifact keys are risky because many plans use predictable names.

Improvement path:

1. Add an internal artifact id.
2. Scope aliases by `session_id`, `project`, or both.
3. Use a unique constraint like `(session_id, key)` for session-local artifacts.
4. Add explicit global/pinned memory promotion instead of accidental global keys.

### 4. Security Policy Wraps Unrestricted Primitives

Best practice:

Powerful tools should be constrained by design, not only screened by regex. File tools should enforce allowed roots. Shell execution should avoid `shell=True` when possible, or be isolated in a sandbox with explicit capabilities.

Current gap:

`bash_exec` uses `subprocess.run(..., shell=True)`. File tools can read/write arbitrary paths. `ActionGuard` uses regex to block/escalate dangerous patterns but does not enforce a workspace root or structured shell AST. `write_file` escalates only for sensitive path regex matches, not for all paths outside the workspace.

Impact:

- A missed regex pattern can run dangerous commands.
- Symlinks/path traversal are not centrally handled.
- Safety behavior differs across tools.
- Approval cache keys can approve broad command strings or paths without a full policy model.

Improvement path:

1. Add a `PolicyEngine` with structured decisions: allow, deny, require approval.
2. Add `PathPolicy` with workspace roots, allowed external roots, symlink resolution, and read/write/delete scopes.
3. Replace common shell operations with dedicated tools where possible.
4. For shell, parse command segments or run through a sandbox wrapper.
5. Make network, filesystem, subprocess, eval, and destructive side effects explicit tool metadata.
6. Persist all approvals with scope, expiry, and reason.

### 5. Tool Execution Logic Is Duplicated

Best practice:

Plan execution and direct execution should share one tool-call executor that handles authorization, guard checks, prompt-injection warnings, result truncation, error classification, logging, and messenger updates.

Current gap:

`DirectExecutionStage` and `ExecutionStage._run_step()` both implement their own tool loop. The prompt-injection gate, guard execution, result handling, repeated-call detection, and force-end behavior are duplicated.

Impact:

- Fixes can land in one path but not the other.
- Security-sensitive behavior can diverge.
- Testing all execution behavior requires testing two loops.

Improvement path:

1. Extract `ToolCallExecutor`.
2. Extract `ToolLoopController` for iteration/tool-call limits and force-end decisions.
3. Keep plan-specific authorization in a policy input, not in a separate loop.
4. Add shared tests for blocked, escalated, failed, injected, repeated, and over-limit tool calls.

### 6. Interactive Input Leaks Into Runtime Stages

Best practice:

All user interaction should go through a gate/input abstraction that can be swapped for CLI, API, test, or headless execution.

Current gap:

The pipeline has a `user_input_fn` for `ASK_USER`, and `CLIUserGate` handles guard escalation. But injection handling inside `DirectExecutionStage` and `ExecutionStage` calls `input()` directly. `main.py` also prompts for workflow candidate approval directly.

Impact:

- Headless/API usage can block unexpectedly.
- Tests cannot drive these branches cleanly.
- UX and audit behavior are scattered.

Improvement path:

1. Extend `UserGate` to support prompt types: approval, choice, free-text clarification.
2. Pass it everywhere user input is needed.
3. Add `AutoDenyGate`/`NonInteractiveGate` behavior for all prompt paths.
4. Record all user decisions in structured events.

### 7. Web Prompt-Injection Handling Defaults Open On Inspector Failure

Best practice:

Fetched untrusted content should stay quarantined until it passes inspection. If inspection fails, the system should fail closed or require explicit user approval depending on mode.

Current gap:

`WebInspector.inspect()` defaults to safe if the LLM inspection layer errors. Layer 1 regex can also false-positive educational content because it immediately returns unsafe without asking Layer 2 to distinguish discussion from instruction. `ReadUrlTool` stores content as an artifact before returning the unsafe warning.

Impact:

- Inspector outage can let risky content through.
- Educational/security content can be blocked too aggressively.
- Unsafe content is present in the artifact store before approval, even if later expelled on denial.

Improvement path:

1. Represent fetched content with a quarantine state.
2. Do not expose quarantined artifacts through normal `get_artifact` until approved.
3. If Layer 2 fails, return `requires_approval` instead of `safe`.
4. Run LLM inspection for regex hits when the content appears educational or quoted.
5. Store inspection results and user approvals as artifact metadata.

## Pattern-Level Roadmap

### Phase 1: Stabilize Contracts

- Add test fixtures for providers, stages, plans, guards, and artifact store.
- Generate planner prompt action types from `ActionType`.
- Add provider capability metadata.
- Convert tool results and monitor inputs to structured status objects.
- Clean dependency declarations and remove generated/state files from git.

### Phase 2: Unify Persistence

- Create one canonical session id.
- Pass it through artifact store, SQLModel DAL, logs, metrics, and runtime context.
- Split artifact store repositories.
- Fix artifact key scoping.
- Add integration tests for session joins and resume behavior.

### Phase 3: Harden Execution

- Extract shared tool executor.
- Add path policy and tool side-effect metadata.
- Replace direct `input()` calls with `UserGate`.
- Add policy audit events.
- Add noninteractive mode that cannot block on prompts.

### Phase 4: Make Memory Safer And More Useful

- Add artifact lineage and typed artifact schemas.
- Add quarantine states for fetched content.
- Promote approved workflow candidates into workflow templates or generated workflow specs.
- Add project memory operations: list, inspect, pin, archive, forget.

### Phase 5: Observability And Evaluation

- Emit structured runtime events.
- Build replay tests from logs/metrics.
- Track provider cost/latency/error rates.
- Use council and monitor decisions as evaluation datasets.

## Suggested Immediate Work Items

1. Fix packaging and dependency drift.
2. Introduce canonical session identity and repair artifact persistence to use it.
3. Extract `ToolCallExecutor` from the two execution stages.
4. Add `PathPolicy` and enforce it in all file tools.
5. Replace direct `input()` in stages with `UserGate`.
6. Add tests for `Pipeline` status transitions and `ActionGuard`.
7. Split `artifact_store.py` along repository/service boundaries.

## Verification Notes

- `python -m compileall -q src` could not run because `python` is not configured in pyenv.
- `python3 -m compileall -q src` completed successfully.
- No `_plans` document contents were read while producing this review.
