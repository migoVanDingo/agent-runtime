# 0064 — Refactor plan v2 (phased implementation)

> Replans 0053 against the current code state from 0063. Codex
> shipped the plumbing for sandbox, path policy, events, json_extract,
> identity dataclass, capabilities, ToolResult — and skipped the four
> structural refactors that change the architecture. This document
> defines the next eleven phases as concrete, sequenced units of work.
>
> Each phase is independently shippable, has explicit exit criteria,
> and lists the files it touches and the tests it requires. An agent
> picking up a single phase should be able to implement it from this
> document plus the existing code; references back to 0051/0053/0063
> are noted where they add context but should not be required reading.

---

## 0. Ordering and dependencies

```
A. Cleanup + pytest + first tests       ─┐ independent of everything
                                          │
B. Identity through PipelineContext     ─┐ B → C   (events need correlation)
C. Thick event coverage                 ─┘
D. Redactor + privacy enforcement       ─  C → D
                                            
E. Outer ToolLoop extraction            ─  C → E (events get inherited via the loop)

F. Provider capabilities + Anthropic     ─  independent
G. Sandbox hardening                    ─  independent

H. Container / DI                       ─  E → H (smaller stages → less churn)
I. Plan vs PlanRun + cleanup pass       ─  H → I (container makes refactor cleaner)

J. Persistence consolidation onto ORM    ─  H, I → J (DAL injection + PlanRun persistence)
K. Dataset loader + parquet export       ─  C, D → K (events + redaction first)
```

Critical path: **A → B → C → D → E → H → I → J**. F, G, K can run
in parallel with whatever's on the critical path at the time.

Effort sizing (XS = afternoon, S = day, M = 2–4 days, L = week+):
A=S, B=S, C=M, D=S, E=M, F=S, G=S, H=M, I=S, J=L, K=M.

---

## Phase A — Cleanup sprint and test substrate

**Why first.** Removes drag for every other phase. Most items are XS;
the test substrate is the load-bearing piece.

**Scope.**

- Delete `_projects/` (entire tree).
- Delete `runtime/classifier.py::IntentClassifier` (the `WorkflowSelector`
  in the same file stays — it's the only live consumer).
- Rewrite `README.md` to describe what the system actually is: a
  multi-stage agent runtime with structured events, sandboxed shell,
  artifact memory, council-reviewed planning. Drop curriculum framing.
- Migrate `tests/test_runtime_phase0.py` from `unittest` to `pytest`.
  Add `pyproject.toml` test config and a `Makefile`/`scripts/test.sh`.
- First wave of pure-logic unit tests (target: 30+ cases):
  - `runtime/critic.py::PlanCriticAdapter.synthesize` — parametrized
    across the eight branches in §4.4 of 0051.
  - `runtime/context_manager.py::_pack_chronological` — pair atomicity,
    fidelity downgrades, plan-window flooring.
  - `runtime/validator.py::PlanValidator.validate`.
  - `runtime/entity_critic.py` + `stages/entity_critic._is_suspicious_candidate`.
  - `workflows/implementations/*.py` regex matchers.
  - `runtime/policy/paths.py` — symlink, `..`, expanduser cases.
- Update CI hook (`scripts/test.sh`): runs pytest from `tests/`, exits
  non-zero on failure.

**Files touched.** `_projects/` (deleted), `README.md`,
`runtime/classifier.py`, `tests/`, `pyproject.toml`, `scripts/test.sh`
(new), `Makefile` (new or updated).

**Exit criteria.**

- `_projects/` does not exist.
- `IntentClassifier` symbol is gone; `grep -rn "IntentClassifier"` returns no hits.
- README's first paragraph describes what the system *is*, not a
  curriculum.
- `pytest tests/ -q` passes ≥30 cases; `make test` runs the same.

---

## Phase B — Identity through PipelineContext

**Why.** `runtime/events/runtime.py` keeps `_identity` as a module-level
global mutated by `set_runtime_identity()`. The Council uses
`ThreadPoolExecutor`; thread workers race on the global. Every event
today has `pipeline_run_id: null`, `plan_id: null`, `step_run_id: null`
— no causal joins are possible.

**Scope.**

- Add `identity: RuntimeIdentity` to `PipelineContext` (in
  `runtime/pipeline_context.py`).
- `Pipeline.run` mints `pipeline_run_id` on entry:
  `context.identity = context.identity.for_pipeline()`.
- Every `Stage.run(context)` reads `context.identity` and emits with it.
- Stage-specific minting in the run methods:
  - `RoutingStage` — no new ID, inherits.
  - `WorkflowMatchStage` — no new ID.
  - `PlanningStage` — `context.identity = context.identity.for_plan()`
    on plan creation.
  - `EntityCriticStage`, `ValidatorStage`, `CouncilStage` — inherit.
  - `ExecutionStage` — `for_plan_run()` at start; `for_step_run()`
    inside the per-step loop; `for_tool_call()` inside the tool-call
    body.
- `Council.deliberate` accepts an `identity: RuntimeIdentity`
  parameter; `_query_one` uses it for events emitted in workers (no
  more global read).
- `runtime/events/runtime.py::get_runtime_identity()` becomes a
  *fallback* used only by code outside a pipeline run (e.g.
  `main.py`'s session-level events).
- Every existing event-emit site updated to read identity from the
  context-bound source.

**Files touched.** `runtime/pipeline_context.py`, `runtime/pipeline.py`,
`runtime/stages/*.py`, `runtime/council.py`, `runtime/tool_executor.py`,
`runtime/events/runtime.py`, `main.py`.

**Tests.**

- Unit: `Pipeline.run` mints a pipeline_run_id and propagates it to
  every stage's `StageResult.updated_context.identity`.
- Unit: Council deliberation in `mode=independent` with N=3
  emits decisions whose events all share the same `pipeline_run_id`
  but have distinct `tool_call_id` per query. (Use a fake provider
  + a `_CollectSink`.)
- Integration: a recorded session JSONL has every `tool.call.completed`
  event populated with `pipeline_run_id`, `plan_id`, `plan_run_id`,
  `step_run_id`, `tool_call_id`.

**Exit criteria.**

- No production code calls `get_runtime_identity()` from inside a
  stage. (Only `main.py` and the legacy fallback path.)
- `_events/{sid}.jsonl` lines for tool calls have non-null IDs at
  every level the call sits inside.
- A test verifies thread-pool council emission preserves
  `pipeline_run_id` per worker.

---

## Phase C — Thick event coverage

**Why.** Today the JSONL has six event types; you can't answer "which
stages cost the most tokens?" or "what was the council verdict for
plan X?" or "did the user approve or deny the most recent escalation?"
With Phase B's identity wiring, adding emission sites becomes
mechanical — but they have to be added.

**Scope.** Add typed events at non-duplicated call sites only (the
duplicated tool-loop sites get covered by Phase E):

- `runtime/pipeline.py::Pipeline._run_stage`:
  - `stage.started` (stage_name, identity).
  - `stage.finished` (stage_name, status, duration_ms, retry_count).
- `providers/base.py::BaseProvider.chat` — wrap subclass implementations
  in a non-abstract `chat()` that emits `llm.call.started` /
  `llm.call.completed` (provider, model, label, prompt_token_estimate,
  response_tokens, latency_ms, stop_reason). Subclasses now implement
  `_chat_impl`. Anthropic and OpenAI-compat both pick this up for free.
- `runtime/council.py`:
  - `council.deliberation.started` (mode, councillor_labels, risk).
  - `council.round.completed` (round_number, decisions_summary, converged).
  - `council.synthesis.completed` (final_verdict, agreement_map,
    synthesis_trace).
- `runtime/escalation.py::CLIUserGate.prompt`:
  - `escalation.requested` (source, reason, tool_name).
  - `escalation.resolved` (approved: bool, source).
- `runtime/stages/planning.py`:
  - `plan.created` (n_steps, requires_synthesis, action_types).
- `runtime/stages/council.py`:
  - `plan.revised` (challenges, surviving_steps).
- `runtime/stages/execution.py` (top-level — not inside `_run_step`):
  - `step.started` (step_index, action_type, tool, description_preview).
  - `step.completed` (step_index, status, duration_ms,
    importance_score).
  - `step.failed` (step_index, error_class, retry_count).
  - `replan.triggered` (failed_step, reason).
- `runtime/sandbox/manager.py::SandboxManager.run_shell`:
  - `sandbox.run` (backend, isolation, network, exit_code,
    duration_ms, timed_out).
- `runtime/persistence.py` — emit `persistence.error` rather than
  silently swallowing.

Define `_plans/0064-events-schema.md` (or `observability/SCHEMA.md`):
one section per event type listing fields, privacy class, and example
payload.

**Files touched.** `runtime/pipeline.py`, `providers/base.py`,
`providers/anthropic.py`, `providers/openai_compat.py`, `runtime/council.py`,
`runtime/escalation.py`, `runtime/stages/planning.py`,
`runtime/stages/council.py`, `runtime/stages/execution.py`,
`runtime/sandbox/manager.py`, `runtime/persistence.py`, new schema doc.

**Tests.**

- Unit: a recorded `Pipeline.run` over a fake stage list emits
  `stage.started`/`stage.finished` for every stage with
  matching `pipeline_run_id`.
- Unit: a `BaseProvider` subclass wraps `_chat_impl` and emits
  `llm.call.completed` with token counts.
- Unit: a council run with a fake adapter emits one
  `council.deliberation.started` + N `council.round.completed` +
  one `council.synthesis.completed`.
- Integration: a real session's JSONL has events of every declared
  type — `grep -c '"event_type"' _events/{sid}.jsonl` ≥ 30 for a
  multi-step plan.

**Exit criteria.**

- The "I want a complete causal trace of this session" use case is
  answerable: every step has a `step.*` event chained to a `plan.*`
  event chained to a `stage.*` event chained to a `pipeline_run_id`.
- `observability/SCHEMA.md` documents every event type.

---

## Phase D — Redactor + privacy enforcement

**Why.** Privacy classification is currently a constant label
(`{classification: "internal", redacted: true}`) with no actual scrubbing
behind it. As soon as exports leave a single user's machine, raw
content (user messages, tool inputs, tool outputs) ships with them.

**Scope.**

- New `runtime/events/redactor.py`:
  ```python
  class Redactor(Protocol):
      def redact(self, event: RuntimeEvent) -> RuntimeEvent: ...

  class RegexRedactor:
      def __init__(self, rules: list[RedactionRule]): ...
  ```
- Default rules:
  - API key prefixes (sk-, ANTHROPIC_API_KEY=, OPENAI_API_KEY=, GROK_,
    DEEPSEEK_, GEMINI_, BRAVE_).
  - Bearer tokens, JWTs, common credential patterns.
  - `~/`, `/Users/<name>/`, `/home/<name>/` → `~user/`.
  - Email addresses → `<email>`.
  - IP addresses → `<ip>`.
- Privacy classes: `public`, `user-content`, `internal`. Per-event
  field tagging; the redactor's behaviour depends on class:
  - `public`: pass through.
  - `internal`: scrub credentials only.
  - `user-content`: scrub credentials + paths + identifiers.
- `EventBus` invokes the redactor before each sink emits, gated by
  config:
  - `runtime.events.redact_on_emit: false` (default — local fidelity).
  - `runtime.events.redact_on_export: true` (default — exports safe).
- The export script (`scripts/export_events.py`) and the future
  loader (Phase K) always apply redaction regardless of the
  `redact_on_emit` flag.

**Files touched.** `runtime/events/redactor.py` (new),
`runtime/events/bus.py`, `runtime/events/schema.py` (privacy class
defaults per event type), `runtime/events/runtime.py`, `config.py`
(`EventsConfig` extended), `config.yml`, `scripts/export_events.py`.

**Tests.**

- Unit: each rule class scrubs its target string in isolation.
- Unit: `RegexRedactor.redact(event)` over an event with API keys in
  payload → keys replaced; structure preserved.
- Round-trip: export script over a fixture session produces a CSV
  with no secrets even when source JSONL contains them.

**Exit criteria.**

- Round-trip test passes: planted secrets in the source events do
  not appear in the exported CSV.
- Privacy classification on each event type is documented in
  `observability/SCHEMA.md` (Phase C).

---

## Phase E — Outer ToolLoop extraction

**Why.** This is the real Phase 3 from 0053. The `ToolCallExecutor`
codex extracted handles ~60 lines of inner duplication; the outer
~350 lines of loop machinery (iteration cap, tool-call cap, force_end,
repeat detection, error correction, max-tokens patching, prompt-injection
quarantine, message append, importance scoring hooks) are still
copy-pasted across `ExecutionStage._run_step` and
`DirectExecutionStage._run_loop`.

**Scope.**

- New `runtime/tool_loop.py`:
  ```python
  @dataclass
  class ToolLoopConfig:
      max_iterations: int
      max_tool_calls: int
      max_consecutive_errors: int
      tool_result_truncate_chars: int

  class ToolLoopHooks(Protocol):
      def on_iteration_start(self, state: ToolLoopState) -> None: ...
      def on_authorization_failure(self, name: str, state: ToolLoopState) -> ToolResult: ...
      def on_tool_complete(self, name: str, result: ToolResult, state: ToolLoopState) -> None: ...
      def on_max_tokens(self, state: ToolLoopState) -> None: ...
      def should_force_end(self, state: ToolLoopState) -> bool: ...

  class ToolLoop:
      def __init__(self, provider, registry, messenger, context_mgr,
                   tool_executor: ToolCallExecutor, injection_gate,
                   spinner, config: ToolLoopConfig, event_bus): ...
      def run(self, *, system: str, tools: list[dict],
              authorized_tool_names: set[str] | None,
              query: str,
              hooks: ToolLoopHooks) -> ToolLoopResult: ...
  ```
- `ExecutionStage._run_step` becomes ≤80 lines: builds tools, builds
  system prompt, instantiates `ToolLoop` with step-level hooks
  (authorization, importance scoring, monitor invocation), reads
  `ToolLoopResult`.
- `DirectExecutionStage._run_loop` becomes ≤80 lines: builds tools,
  instantiates `ToolLoop` with direct-mode hooks (router select per
  iteration, error-correction injection).
- Prompt-injection quarantine moves into the loop and routes through
  `runtime/injection_gate.handle_injection_warning` (already exists).
- The loop emits the in-loop events that Phase C couldn't: `iteration.started`,
  `tool_loop.force_end_triggered`, `tool_loop.max_tokens_recovered`.

**Files touched.** `runtime/tool_loop.py` (new),
`runtime/stages/execution.py`, `runtime/stages/direct_execution.py`.

**Tests.**

- Unit: `ToolLoop.run` over a stub provider that returns a sequence
  of canned responses (text, tool_use, tool_use, end_turn) terminates
  correctly.
- Unit: identical-tool-call-twice triggers `force_end`.
- Unit: 3 consecutive errors triggers the consecutive-error
  injection.
- Unit: max_tokens with dangling tool_use produces synthetic
  tool_results so the message history stays valid.
- Unit: an unauthorized tool name in `authorized_tool_names` produces
  a rejection `tool_result` with the correct error code.
- Parity: a small set of pre-recorded sessions produces equivalent
  final responses through the new loop (run before/after, diff).

**Exit criteria.**

- `ExecutionStage` and `DirectExecutionStage` are each ≤200 lines
  (including the `Stage.run` wrapper).
- `runtime/tool_loop.py` ≤300 lines and has no test imports of
  stage-specific code.
- Parity tests pass.

---

## Phase F — Provider capabilities + Anthropic structured output

**Why.** `ProviderCapabilities` exists but no caller consults it.
`json_extract` exists but only `critic` uses it. Anthropic still
ignores `json_schema`. Five hand-rolled JSON parsers are still
fragile.

**Scope.**

- Implement Anthropic structured output via the single-tool trick.
  In `AnthropicProvider.chat`, when `json_schema` is provided:
  1. Build a synthetic `respond` tool whose `input_schema` matches.
  2. Set `tool_choice={"type": "tool", "name": "respond"}`.
  3. Force tool use; parse the tool input as the response JSON.
- Set `AnthropicProvider.capabilities.structured_json_schema = True`.
- Migrate the five hand-parsers to either structured output (when
  `provider.capabilities.structured_json_schema=True`) or
  `json_extract` (fallback):
  - `runtime/monitor.py::ExecutionMonitor._parse`.
  - `runtime/classifier.py::WorkflowSelector._parse`.
  - `runtime/importance.py::ImportanceScorer._parse`.
  - `planning/planner.py::Planner._parse`.
  - `runtime/utils.parse_routing_response` (the JSON inside the
    `<route>` tag — the regex extraction stays as-is).
- Each consumer declares its expected schema once; they're kept
  alongside the consumer (e.g. `MONITOR_DECISION_SCHEMA` in
  `runtime/monitor.py`).

**Files touched.** `providers/anthropic.py`, `providers/capabilities.py`,
`runtime/monitor.py`, `runtime/classifier.py`, `runtime/importance.py`,
`planning/planner.py`, `runtime/utils.py`.

**Tests.**

- Unit: `extract_json` extracts every fixture from a recorded set
  of malformed model outputs (drops, duplicate keys, leading prose,
  fenced blocks, etc.).
- Integration (opt-in, real Anthropic): force `json_schema` through
  Anthropic; verify the `respond` tool path returns parsed JSON.
- Unit: each migrated parser rejects malformed input gracefully
  (returns the documented safe default, doesn't raise).

**Exit criteria.**

- `grep -rn "json.loads\|json.JSONDecodeError" src/runtime/ src/planning/`
  returns only the `json_extract` module and intentional uses (e.g.
  artifact_store de/serialisation).
- Anthropic structured output verified end-to-end.
- All five consumers route through one of: structured output via
  capabilities, or `json_extract`.

---

## Phase G — Sandbox hardening

**Why.** Codex's sandbox is functional but has three real problems:
per-call docker startup latency (~200–500 ms), binary network policy,
and auto-fallback under explicit `backend: docker`. 0053 §6.1
specified a different shape; we should converge.

**Scope.**

- New `runtime/sandbox/long_lived_docker.py`:
  - One container per session, started in `init_runtime_sandbox()`
    (called from `main.py` after session id is known).
  - `run_shell` uses `docker exec` against the running container.
  - Container is torn down on session end.
  - Falls back to per-call `DockerShellBackend` (existing) if
    long-lived container fails to start.
- New `runtime/sandbox/mac_sandbox.py`:
  - Wraps `sandbox-exec(1)`. Profile pinned to project workdir; no
    network by default.
  - Used when `backend: auto` resolves on macOS without docker.
- Update `SandboxManager`:
  - Backend resolution: `auto` (new value, becomes the recommended
    default) → docker if available → mac_sandbox if on macOS → host
    with WARN.
  - `backend: docker` → fail fast on infrastructure failure (no
    auto-fallback regardless of `allow_host_backend`).
  - `backend: host` → loud-warning host execution.
- Network policy:
  - `none` (current behaviour, `--network none`).
  - `outbound` (no `--network` flag — default docker bridge).
  - `restricted` (allowlist of hosts via docker network policies — TBD;
    can land as a no-op stub in this phase with a TODO).
- Per-call host-execution escalation:
  - When the model invokes `bash_exec` and the request must run on
    the host (e.g. needs unsandboxed access), `Escalation(source="sandbox", ...)`
    is raised. Approval is *not* cached (one-shot only).
- Sandbox events emitted (already in Phase C scope).

**Files touched.** `runtime/sandbox/long_lived_docker.py` (new),
`runtime/sandbox/mac_sandbox.py` (new), `runtime/sandbox/manager.py`,
`runtime/sandbox/base.py` (network enum), `config.py` (SandboxConfig),
`config.yml`, `main.py` (lifecycle), `runtime/escalation.py` (new
source kind).

**Tests.**

- Unit: `SandboxManager` resolves `backend: auto` correctly under
  three mocked environments (docker available; macOS only; neither).
- Unit: `backend: docker` with simulated infra failure raises, does
  not fall back even when `allow_host_backend=true`.
- Integration (opt-in, real docker): long-lived backend starts a
  container, executes 5 commands via `docker exec`, tears down. Each
  call's `duration_ms` is at least 5× lower than the per-call backend.
- Integration: `bash_exec rm -rf /tmp/sandbox-test/*` cannot affect
  host paths under any sandbox backend.

**Exit criteria.**

- `backend: auto` is the default in `config.yml`.
- `backend: docker` fails fast on infra failure.
- Long-lived docker latency benchmark in tests shows ≥5× improvement
  over per-call.
- macOS users without docker get a working sandbox.
- Sandbox escalation flow verified: user can approve a one-shot host
  execution; approval is not cached for subsequent calls.

---

## Phase H — Container / Dependency Injection

**Why.** 29 files still `from app_config import config` at module top.
Tests can't construct an Agent with a fake config. Two Agents can't
run side-by-side. Every later phase wants to add a constructor
parameter (Sandbox, EventBus, ORM session) and the choice is "add a
new global" or "add a real container". Make the right one available
now.

**Scope.**

- New `runtime/container.py`:
  ```python
  @dataclass
  class Container:
      config: AppConfig
      settings: Settings
      provider: BaseProvider
      runtime_provider: BaseProvider
      registry: ToolRegistry
      router: StaticRouter
      embeddings: EmbeddingModel | None
      artifact_store: ArtifactStore | None
      event_bus: EventBus
      sandbox: SandboxManager
      metrics_writer: CouncilMetricsWriter | None
  ```
  Plus a `Container.build(...)` classmethod that wires defaults.
- `main.py`:
  - Build `Container` once per process.
  - Pass to `Agent(container=...)`.
- `Agent.__init__` reads container fields rather than importing
  module-level globals. The pipeline construction in `_build_pipeline`
  takes container as input.
- Each stage's constructor signature gains the typed slice it needs
  (e.g. `EventBus`, `Sandbox`, but not the whole container — the
  container is the *wiring* point, not the runtime DI service).
- Replace inline imports inside hot loops with top-level imports.
  Real cycles get broken by extracting an interface module
  (e.g. `Messenger`-as-payload removed from
  `ContextManager._compress_tool_result`).
- `app_config.py` keeps the YAML/env parsing logic but stops
  exposing `config`/`settings` at import time. Modules that need a
  tunable take it through their constructor.

**Files touched.** `runtime/container.py` (new), `main.py`,
`agent.py`, every stage in `runtime/stages/*.py`, `app_config.py`,
modules that previously read `from app_config import config` at
module top.

**Tests.**

- Unit: `Container.build()` produces a valid container from a
  loaded `AppConfig` + `Settings` pair.
- Integration: two `Agent` instances with different configs run
  side-by-side in a single test, each producing its own JSONL
  event file.
- Lint: `grep -rn "^from app_config import config" src/` returns
  zero hits in stage and runtime modules.

**Exit criteria.**

- Two Agents can coexist in one process with different configs.
- `from app_config import config` only appears in the container
  builder and `main.py`.
- No new module-level globals introduced by this phase.

---

## Phase I — Plan vs PlanRun + cleanup pass

**Why.** Combines four small independent items into one phase.

**Scope.**

1. **Plan vs PlanRun split.**
   - `planning/schema.py` keeps `Plan`, `Step`, `ActionType`,
     `PLAN_JSON_SCHEMA`. Removes `StepStatus`, `result`, `error`,
     runtime fields from `StepFlags`.
   - New `runtime/run_state.py`:
     ```python
     @dataclass
     class StepRun:
         spec: Step
         status: StepStatus = PENDING
         result: str | None = None
         error: str | None = None
         retry_count: int = 0
         deferred: bool = False
         skipped: bool = False

     @dataclass
     class PlanRun:
         spec: Plan
         steps: list[StepRun]
         replan_count: int = 0
     ```
   - `ExecutionStage` operates on `PlanRun`; converts at entry.
   - Replan produces a new `Plan` → wrapped in a fresh `PlanRun`.
2. **Magic numbers → config.**
   - Move `_DIRECT_MAX_TOOL_RESULT_CHARS`, `_DIRECT_MAX_TOOL_CALLS`,
     `_DIRECT_MAX_CONSECUTIVE_ERRORS`, `_DIRECT_MAX_ITERATIONS` from
     `runtime/stages/direct_execution.py` (now `runtime/tool_loop.py`)
     into `RuntimeConfig.tool_loop`.
   - Move `_MAX_RETRIES_PER_STAGE`, `_MAX_ASK_USER_PER_STAGE` from
     `runtime/pipeline.py` into `RuntimeConfig.pipeline`.
3. **Retry-budget map.**
   - New `docs/retry-budget.md` documenting how stage retries, step
     retries, monitor decisions, ASK_USER caps, tool_loop caps, and
     direct-mode caps interact. One paragraph per cap; one diagram.
4. **Path policy escalation.**
   - `PathPolicyDecision.allowed=False` produces an `Escalation(source="path_policy", ...)`
     when the policy config has `escalate_on_deny: true` (default).
     The current outright-deny is the fallback for non-interactive
     environments.
5. **Direct-mode tool authorization.**
   - When `DirectExecutionStage` is invoked as the ABORT fallback
     from a high-risk classification, restrict to a read-only
     toolset list (configurable: `runtime.fallback.high_risk_toolsets`).
6. **Logger module split.**
   - `LogFormatting` class owns palette state.
   - `configure_logging` stops calling `init_metrics_writer` —
     metrics writer is built by the Container.

**Files touched.** `planning/schema.py`, `runtime/run_state.py` (new),
`runtime/stages/execution.py`, `runtime/persistence.py`,
`runtime/pipeline.py`, `runtime/tool_loop.py` (from Phase E),
`runtime/stages/direct_execution.py`, `runtime/policy/paths.py`,
`runtime/escalation.py`, `logger.py`, `config.py`, `config.yml`,
`docs/retry-budget.md` (new).

**Tests.**

- Unit: `Plan` has no `status` / `result` / `error` fields.
  Constructing one with those keys raises `TypeError`.
- Unit: `PlanRun.from_plan(plan)` produces fresh `StepRun`s with
  `PENDING` status.
- Unit: replan produces a new `Plan` and a new `PlanRun`; the old
  `Plan` is unchanged.
- Unit: high-risk fallback produces a tool list that excludes
  `bash_exec`, `write_file`, `delete_file`, etc.
- Unit: path policy denial produces an `Escalation` (when configured)
  rather than an error string.

**Exit criteria.**

- No mutation of `Plan.steps` outside `Plan.__init__`.
- `grep -rn "_DIRECT_MAX\|_MAX_RETRIES_PER_STAGE\|_MAX_ASK_USER_PER_STAGE" src/`
  returns zero hits.
- `docs/retry-budget.md` exists and is linked from README.

---

## Phase J — Persistence consolidation onto ORM

**Why.** Two persistence stories diverging:
`runtime/artifact_store.py` (1278 lines of raw SQLite at
`_store/artifacts.db`) and the SQLModel ORM at `data/agent.db`. The
former is what the agent actually uses; the latter is best-effort
audit. Long-term they need to merge.

**Scope.**

- Add ORM models in `db/models/`:
  - `Artifact` (extend the placeholder): kind, summary, source,
    session_id, created_at, last_accessed, access_count,
    decay_score, permanent, summary_embedding (BLOB),
    project_tag, data_path, value (TEXT, optional inline).
  - `ArtifactSession` (action log).
  - `ConversationMessage`.
  - `Request` (workflow-discovery clustering).
  - `WorkflowCandidate`.
  - `SessionSummary`.
  - `Event` (best-effort mirror of the JSONL — primary store stays
    JSONL).
- DAL methods in `db/dal/artifact_dal.py` for every operation the
  current `ArtifactStore` exposes: `set/get/meta/list/expel/expel_pattern/pin`,
  `record_request/discover_workflows`, `recall_sessions`,
  `recall_artifacts`, `apply_decay`, `set_active_project`, embedding
  storage and cosine search (with sqlite-vec when available, python
  cosine fallback otherwise).
- New `runtime/artifact_store/` package (replaces the single file):
  ```
  runtime/artifact_store/
    __init__.py          (re-exports ArtifactStore, ArtifactMeta, etc.)
    facade.py            (ArtifactStore — thin wrapper over DAL)
    file_store.py        (on-disk file management, parquet/txt)
    decay.py             (decay sweep — DAL-backed)
    discovery.py         (workflow discovery clustering — DAL-backed)
    recall.py            (RAG recall — DAL-backed)
  ```
- Alembic migration that imports rows from `_store/artifacts.db`
  into `data/agent.db`. The on-disk artifact files (`_store/data/*`)
  stay where they are — only metadata moves.
- `_store/artifacts.db` removed at the end of the migration; the
  on-disk file directory is repointed to `data/artifacts/` (or kept
  at `_store/data/`, configurable).

**Files touched.** `db/models/{artifact,artifact_session,conversation_message,request,workflow_candidate,session_summary,event}.py`,
`db/dal/artifact_dal.py`, `runtime/artifact_store/` (new package
structure), `src/alembic/versions/000X_artifact_store_consolidation.py`
(new migration), `runtime/artifact_store.py` (deleted in favour of
the package), all consumers of `ArtifactStore` (no API change at
the consumer layer).

**Tests.**

- Unit: each DAL method round-trips through the ORM with the same
  semantics as the old SQLite version.
- Migration test: a snapshot of `_store/artifacts.db` (fixture)
  imports into a fresh `data/agent.db` with no row count discrepancy.
- Integration: a recorded session that exercises set/get/recall/decay
  against the new façade produces the same artifacts as the old
  store.

**Exit criteria.**

- `runtime/artifact_store/__init__.py` re-exports the same public API
  as the old `runtime/artifact_store.py`.
- No file in `runtime/` calls `sqlite3.connect`.
- A successful Alembic migration on a real `_store/artifacts.db`.
- `_store/artifacts.db` does not exist after migration.
- All existing tests that touch the artifact store still pass.

---

## Phase K — Dataset loader + parquet export

**Why.** Closes the dataset story. With Phases C+D in place, the
JSONL is rich and redacted; this phase makes it queryable.

**Scope.**

- New `observability/loader.py`:
  ```python
  def load_session(session_id: str) -> pd.DataFrame: ...
  def load_sessions(since: datetime | None = None,
                    until: datetime | None = None,
                    project: str | None = None) -> pd.DataFrame: ...
  def tool_calls_for(session_id: str) -> pd.DataFrame: ...
  def llm_calls_for(session_id: str) -> pd.DataFrame: ...
  def joined_session_summary(session_id: str) -> pd.DataFrame: ...
  ```
- Per-event-type schemas: rather than the current flat CSV with shared
  fields, each event type gets a typed schema. The loader returns
  one frame per event type plus a `joined` view.
- New `scripts/export_dataset.py`:
  ```bash
  python scripts/export_dataset.py --since 30d --out _datasets/2026-05-30.parquet
  python scripts/export_dataset.py --session SESS01XXX --out /tmp/session.parquet
  ```
  - Reads JSONL.
  - Applies redactor (Phase D) regardless of local `redact_on_emit`.
  - Writes parquet partitioned by event_type.
  - Emits a `_manifest.json` with row counts, redaction-rule version,
    schema version.
- `observability/SCHEMA.md` updated with the per-event-type field
  list.
- Replace `scripts/export_events.py` (the CSV one) with a thin
  wrapper around the new exporter that produces CSV for
  back-compatibility.

**Files touched.** `observability/loader.py` (new),
`observability/export.py` (new),
`scripts/export_dataset.py` (new),
`scripts/export_events.py` (rewritten as a thin wrapper),
`observability/SCHEMA.md`,
`pyproject.toml` (add `pandas`, `pyarrow` to a `[dataset]` extra).

**Tests.**

- Unit: loader correctly types each event-type schema (e.g.
  `tool.call.completed` always has `result_bytes: int`,
  `duration_ms: int`).
- Unit: exporter applies redaction even when `redact_on_emit=false`.
- Integration: round-trip — record a small session, export, reload,
  verify equivalence after redaction.

**Exit criteria.**

- `python scripts/export_dataset.py --since 30d` produces a parquet
  file with a `_manifest.json` sibling.
- The "what's the median LLM latency by stage in the last 7 days?"
  question is answerable in two pandas lines.

---

## Cross-cutting hygiene

These apply during every phase:

1. **Tests are part of the phase.** A phase isn't done until its
   tests are green. No "tests next phase".
2. **No new module-level singletons.** From Phase H onward, every
   stateful primitive is constructed by the Container and passed in.
3. **No silent `except Exception`.** Recoverable failures emit a
   typed event (`*.error`) and return a typed result. Genuine
   programming errors propagate.
4. **All new event types appear in `observability/SCHEMA.md`** before
   they ship. Schema-versioned.
5. **Privacy class is set explicitly per emission site.** Defaults are
   conservative (`internal`); raw user content is `user-content`.
6. **Backwards compatibility**. Phases B–E add behaviour behind
   feature flags initially (`runtime.events.enabled`, etc.); flags
   default-on once the phase's tests are green. No deprecation
   cycles — this is a single repo with one user (today).

---

## What we're explicitly *not* doing in this plan

- Async runtime conversion. Worth doing eventually; not here.
- Streaming provider responses.
- Postgres migration. Designed for, not done.
- Multi-tenant deployment, auth, billing.
- Replacing the LLM-driven council with a learned policy.
- Web UI / dashboard for the dataset. The data shape is the
  deliverable; visualisation is a separate effort.

---

## What success looks like at the end

- `pytest tests/ -q` runs >100 cases, ≥80% coverage on `runtime/critic.py`,
  `runtime/context_manager.py`, `runtime/guard.py`, `runtime/validator.py`,
  `runtime/tool_loop.py`, `runtime/events/redactor.py`, `runtime/policy/paths.py`.
- One container per session (long-lived docker), `bash_exec` cannot
  affect host paths.
- `_events/{sid}.jsonl` has 30+ events per multi-step session, every
  one with full identity correlation. No nulls in `pipeline_run_id`,
  `plan_id`, `step_run_id` for events emitted inside their respective
  scopes.
- Privacy-classified redaction round-trip test passes.
- `python scripts/export_dataset.py --since 30d --out X.parquet`
  produces an analysable parquet file.
- Two `Agent` instances run side-by-side in tests with different configs.
- Plan and PlanRun are separate types; no in-place mutation of plans.
- One persistence story: `data/agent.db` + on-disk artifact files; no
  `_store/artifacts.db`.
- `_projects/`, `IntentClassifier`, and the magic-number constants
  are gone.
- Text logs unchanged; humans still love them.

---

## Open decisions before implementation

1. **`core/` vs `runtime/`.** 0053 proposed extracting reusable
   primitives into `core/`. Codex put new packages under `runtime/`
   instead. I think that's fine — `core/` was a curriculum-era
   notion. Recommendation: keep new modules under `runtime/` for
   consistency with what shipped; revisit if we ever need a second
   agent.

2. **Where does `observability/` live?** `runtime/events/` holds
   the bus, schema, and runtime wiring. `observability/` would hold
   redactor, loader, exporter, schema doc. Could fold them all into
   `runtime/events/`. Recommendation: keep them split — events are
   *runtime* state; loader/exporter are *out-of-band tools* that
   read events.

3. **Sandbox network policy "restricted" mode.** Allowlist of hosts
   needs a real implementation (probably docker network + iptables
   rules). Land as a stub now and treat the implementation as a
   later phase, or block Phase G on it? Recommendation: stub now,
   implement when a workflow needs it.

4. **Anthropic single-tool trick vs `tool_choice="any"` JSON mode.**
   Anthropic has different ways to force JSON. Recommendation:
   single-tool trick — works on every model we support today.

5. **Persistence consolidation timing.** It's the largest phase. We
   can defer it until after K (dataset) if we'd rather get the
   dataset story shipped first. Recommendation: do J before K so
   the loader can read either JSONL events *or* the ORM event
   mirror, depending on which is faster for the query.
