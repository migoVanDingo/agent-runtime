# 0053 — Runtime Refactor Design

> Synthesizes the architectural review in 0051 with the user's explicit
> direction (ORM replaces raw SQLite, `_projects` removed, curriculum
> framing not firm, tests required, bash sandboxing, run logs become an
> analysis dataset). Sequenced so each phase ships independently and
> leaves the system more refactorable, not less.
>
> Writes a *design*, not phase plans. Each phase below is a sketch of
> intent, scope, and exit criteria; concrete phase plans (0055+ style)
> are produced from this document, not before it.

---

## 1. Confirmed decisions

| # | Decision                                                                 |
|---|--------------------------------------------------------------------------|
| 1 | The ORM (`db/`) is the long-term persistence story; `runtime/artifact_store.py`'s raw SQLite is interim. |
| 2 | `_projects/` is obsolete — delete it.                                    |
| 3 | Curriculum framing is not firm — extract reusable primitives into a `core/` package without curriculum guilt. |
| 4 | `_tests/` should not be empty. Tests land alongside every refactor.      |
| 5 | `bash_exec` runs raw on the host — must be sandboxed.                    |
| 6 | The text logs are valued; we want a *parallel* structured event stream so prior runs become a dataset for offline analysis (and eventually multi-user analytics). |

These six items frame the rest of this document.

---

## 2. Goals and non-goals

### Goals

- Replace duplicated/fragile patterns with one canonical version each.
- Make the system testable end-to-end without spinning up a real LLM.
- Produce structured events as a first-class artifact of every run.
- Sandbox shell execution by default, with a sensible cross-platform
  story.
- Consolidate persistence onto the ORM so we have one place to query
  for "what happened in session X".
- Keep human-readable text logs intact — they're valuable.

### Non-goals (this design)

- Async-first runtime. Worth doing eventually, but not in scope here;
  it would invalidate too much of the surrounding work.
- Streaming provider responses. Same.
- Replacing the LLM-driven council with a learned policy. We collect
  the data first, then revisit.
- Multi-tenant deployment, auth, billing. Out of scope.
- Postgres migration. Designed *for*, not done in, this refactor.

---

## 3. Guiding principles

1. **Pure logic before glue.** When a function can be tested in
   isolation, it must be. The pipeline + stage shape is already
   pure-ish; the council synthesis, plan validator, action guard,
   workflow regexes, context manager packing, and entity critic
   reverter are all pure logic and should be the first tests.

2. **Structured events are a parallel rail to text logs.** The text
   log stays exactly as it is. We add a JSONL event stream that
   captures the same conceptual moments in machine-readable form.
   Anything a future model or analyst would want to compare across
   runs goes through the event emitter — never through `logger.info`
   alone.

3. **Sandbox by default, escape hatches for the user.** `bash_exec`
   should be sandboxed. A user can opt out per-session
   (`config.sandbox.enabled=false`) or per-call (an escalation that
   says "this command needs host access"), but the default is
   safe. This matches the existing `ActionGuard` mental model: the
   guard says *what's allowed*, the sandbox says *where it runs*.

4. **One container, many stages.** Replace the half-dozen
   module-level singletons with one explicit `Container` (or
   `Runtime`) object that `main.py` constructs and passes to the
   `Agent`. Stages take what they need from constructor injection,
   not module-level imports. This is what makes Phase 1 a real
   improvement instead of cosmetic refactoring.

5. **Persistence is a side rail.** Persistence writes never gate
   user-facing behaviour. The ORM is best-effort, in-process, and
   off the hot path. This is already the contract of
   `PersistenceWriter`; we extend it to events.

6. **Privacy is built in, not bolted on.** Because run data will
   eventually leave a single user's machine (we want to aggregate
   across users), redaction lives in the event emitter, not in a
   downstream "scrub" step. Users can disable redaction locally;
   exports run with redaction on by default.

7. **Tests are part of every phase.** The exit criteria for each
   phase below explicitly require tests for the pure-logic surface
   touched by that phase. Phase 0 also seeds tests for code that
   exists today and is high-leverage.

---

## 4. Target shape

```
src/
  core/                         ← new: domain-agnostic primitives
    pipeline.py                 (moved from runtime/)
    stage_base.py               (moved from runtime/)
    stage_result.py             (moved from runtime/)
    council.py                  (moved from runtime/)
    tool_loop.py                ← Phase 3: shared ReAct loop
    events.py                   ← Phase 5: structured event types
    json_extract.py             ← Phase 2: tolerant JSON parser
    sandbox.py                  ← Phase 4: sandboxed executor protocol
    container.py                ← Phase 1: dependency container

  runtime/
    pipeline_context.py         (stays)
    stages/*.py                 (stays — gets thinner; uses core/)
    classifier.py               (WorkflowSelector only — IntentClassifier deleted)
    critic.py                   (PlanCriticAdapter — uses core/council)
    monitor.py                  (uses core/json_extract)
    guard.py                    (stays; gains tests)
    context_manager.py          (stays; gains tests)
    entity_critic.py            (stays; gains tests)
    importance.py               (uses core/json_extract)
    persistence.py              (extended for events)
    artifact_store/             ← Phase 7: package split
      __init__.py
      core.py
      recall.py
      decay.py
      discovery.py
      schema_sql.py             (interim until ORM cutover)

  db/
    models/                     (stays; gains artifact + event models)
    dal/                        (stays; gains artifact + event DALs)
    engine.py / session.py / sync.py

  observability/                ← new (Phase 5)
    emitter.py                  (writes events)
    redactor.py                 (PII/secret scrubbing)
    loader.py                   (read events into dataframes)
    export.py                   (build parquet datasets from N sessions)

  tools/
    base.py
    registry.py
    toolsets.py
    implementations/...

  workflows/                    (stays)

  planning/
    schema.py                   ← Phase 6: only Plan/Step (spec)
    run_state.py                ← Phase 6: StepRun/PlanRun (execution)
    planner.py / synthesizer.py / prompts.py

_tests/
  unit/
  integration/
  fixtures/
```

The intent is that `core/` becomes a small, well-tested set of domain-agnostic
primitives that the agent uses but does not own. If we ever build a
second agent (a planner-only tool, an evaluator harness), it imports
from `core/` and writes its own `runtime/`.

---

## 5. Phased plan

Each phase is independent, ships its own tests, and produces a
visible win. Phase 0 must land first; the rest can be shuffled if
priorities change.

### Phase 0 — Cleanup + test substrate

Goal: remove obsolete framing, set up the test infrastructure that
every subsequent phase depends on.

**Scope.**

- Delete `_projects/` (curriculum scaffolding; superseded).
- Remove `PLAN.md` reference if it points into `_projects/`.
- Update `README.md` to describe the actual system, not the
  curriculum that was abandoned.
- Add `pyproject.toml` test config (pytest, coverage, ruff if not
  present).
- Create `_tests/{unit,integration,fixtures}/`.
- Land first wave of unit tests against existing pure-logic targets:
  - `runtime/critic.py::PlanCriticAdapter.synthesize` (8+ branches).
  - `runtime/context_manager.py::_pack_chronological` (pair atomicity,
    placeholder/compressed/full transitions, plan-window flooring).
  - `runtime/guard.py::ActionGuard.check_tool_call` and
    `_check_shell_command` (heredoc handling, sensitive paths,
    approval cache).
  - `runtime/validator.py::PlanValidator.validate`.
  - `runtime/entity_critic.py` + the `_is_suspicious_candidate`
    reverter in `stages/entity_critic.py`.
  - `workflows/implementations/*.py` regex matchers.
- Add a Makefile or `scripts/test.sh` that runs the suite.
- Add a `CONTRIBUTING.md` or top-of-`README.md` section documenting
  how tests are organized.

**Exit criteria.**

- `_projects/` gone.
- `pytest _tests/unit -q` passes locally with ≥30 cases across the
  modules above.
- README accurately describes the runtime architecture.

**Why first.** Every other phase becomes safer once we have tests
covering the highest-stakes pure-logic code. Council synthesis and
context packing are the two things most likely to silently regress.

---

### Phase 1 — Dependency container, kill module-level singletons

Goal: replace import-time global state (`config`, `settings`,
`_artifact_store`, `_model`, `_metrics_writer`, `_agent_engine`)
with explicit construction in `main.py`.

**Scope.**

- New `core/container.py`:
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
      metrics_writer: CouncilMetricsWriter | None
      event_emitter: EventEmitter           # added in Phase 5
      sandbox: Sandbox                      # added in Phase 4
  ```
- `main.py` constructs the `Container`, passes it to `Agent(container)`.
- `Agent.__init__` reads container fields instead of importing
  module-level globals.
- Stages take a `Container` (or a typed slice) at construction.
- `app_config.py` keeps the YAML/env parsing logic but stops
  exposing `config = get_config()` at import time. Modules that
  must know a tunable get it from the container or take it as a
  parameter.
- Inline imports inside hot loops (≈25 of them — see 0051 §5.2)
  become top-level imports; real cycles are broken by extracting
  small interface modules.

**Exit criteria.**

- Two `Agent` instances can run side-by-side in the same process
  with different configs (proven by a test).
- `grep -rn "^config = " src/` returns nothing module-level.
- No stage file imports `app_config.config` at import time.

**Why second.** Every later phase wants to add a constructor
parameter (sandbox, emitter, ORM session). Doing that one phase at
a time without a container creates churn. Land the container first.

---

### Phase 2 — JSON contracts and Anthropic structured output

Goal: stop hand-parsing LLM JSON in six places; use the provider's
structured-output mechanism whenever possible.

**Scope.**

- New `core/json_extract.py`: tolerant parser used as fallback only.
  Three strategies: fenced block, balanced-brace walk, raw `loads`.
  Same shape as `runtime/critic.py::_extract_json`, hardened.
- Extend `BaseProvider.chat` contract: `json_schema` is honoured by
  every provider, not just OpenAI-compat. For Anthropic, implement
  via the "single tool" trick — declare a `tool_use` whose schema
  is the desired output and force the model to call it.
- Update consumers to declare schemas:
  - `Planner` (already does — keeps working).
  - `runtime/critic.py` (CriticResult schema).
  - `runtime/monitor.py` (StepAssessment schema).
  - `runtime/classifier.py::WorkflowSelector` (workflow-name schema).
  - `runtime/importance.py` (Importance enum schema).
  - `runtime/utils.parse_routing_response` keeps the regex-tag
    parser because it's a header inside an otherwise free-form
    response — but the JSON inside the tag goes through
    `core/json_extract`.
- Delete dead `IntentClassifier` (its docstring already says it's
  unused).

**Exit criteria.**

- All six LLM-JSON consumers route through one parser and/or
  provider-side structured output.
- A unit test forces malformed JSON through each consumer and
  verifies the failure mode is "log and return safe default", not a
  crash.
- Anthropic structured output verified end-to-end against a real
  model in an opt-in integration test.

**Bonus.** Once payloads are reliably structured, Phase 5's event
emitter can record the parsed object directly instead of the raw
text.

---

### Phase 3 — Extract shared ReAct loop (`core/tool_loop.py`)

Goal: collapse the duplicated tool loop in
`runtime/stages/execution.py` and
`runtime/stages/direct_execution.py`.

**Scope.**

- New `core/tool_loop.py`:
  ```python
  class ToolLoop:
      def __init__(self, provider, registry, messenger, context_mgr,
                   guard, user_gate, spinner, event_emitter, sandbox,
                   *, max_iterations, max_tool_calls, max_consecutive_errors,
                   tool_result_truncate_chars):
          ...

      def run(self, *, system: str, tools: list[dict],
              authorized_tool_names: set[str] | None,
              query: str,
              hooks: ToolLoopHooks) -> ToolLoopResult:
          ...
  ```
  Hooks: `on_step_progress`, `on_authorization_failure`,
  `on_injection_warning`, `on_max_tokens`, `on_step_complete`.
- `ExecutionStage._run_step` becomes ≈40 lines that builds tools,
  builds system prompt, instantiates the loop, and reads the
  `ToolLoopResult`.
- `DirectExecutionStage._run_loop` becomes a similar wrapper.
- Prompt-injection handling moves into the loop and produces an
  `Escalation` rather than embedded `print()`/`input()` (closes
  0051 §6.9).
- Repeated-tool-call detection, max-tokens dangling-tool patching,
  and consecutive-error injection move into the loop.

**Exit criteria.**

- Both stages are <100 lines each.
- One unit test exercises the loop with a fake provider, fake
  registry, and a recorded fixture; verifies termination,
  authorization rejection, repeat detection, and dangling-tool
  patching.
- Behaviour parity verified by running a small set of sample
  queries before and after.

**Why third.** It's the single largest piece of code touched on
every feature change. It also is what every later phase wants to
emit events from (Phase 5) and dispatch shell calls from
(Phase 4).

---

### Phase 4 — Sandboxed bash execution

Goal: `bash_exec` no longer runs on the host by default.

**Design.**

- New `core/sandbox.py`:
  ```python
  class Sandbox(Protocol):
      def run(self, command: str, *, cwd: Path,
              mounts: list[Mount], network: NetworkPolicy,
              timeout_seconds: int, memory_mb: int | None) -> SandboxResult: ...
      def status(self) -> SandboxStatus: ...
  ```
  - `SandboxResult` carries stdout, stderr, exit code, duration,
    sandbox metadata (image, container id, etc.) — emitted as a
    structured event in Phase 5.
- Concrete implementations (chosen at runtime by capability probe):
  1. `DockerSandbox` — preferred, cross-platform. Per-session
     ephemeral container with the project workdir bind-mounted RW
     and `_store/`/`_logs/` excluded by default (configurable
     allowlist). Network policy: `none | outbound | restricted`.
     Resource limits via `--cpus`, `--memory`, `--pids-limit`.
  2. `MacSandboxExec` — macOS native (`sandbox-exec(1)`) for users
     without Docker. Profile pinned to project workdir. No network
     by default.
  3. `HostSandbox` — explicit opt-out. Logs every invocation as an
     event with `sandbox=host` so downstream analysis can flag it.
- `tools/implementations/shell/bash_exec.py` delegates to the
  Container's `Sandbox` instead of calling `subprocess.run`
  directly.
- `ActionGuard.check_tool_call("bash_exec", ...)` continues to BLOCK
  truly dangerous patterns (the regex in `runtime/guard.py`) before
  the sandbox sees them. Sandbox catches what the guard misses; guard
  catches what the sandbox can't (intent).
- A new `Escalation.source = "sandbox"` for cases the user wants
  the host (e.g. "this command needs to install Homebrew packages
  outside the project"). The escalation prompt makes it explicit
  that approval downgrades the call to host execution for *this
  invocation only*; it doesn't turn the sandbox off.
- `config.sandbox`:
  ```yaml
  sandbox:
    backend: auto              # auto | docker | sandbox-exec | host
    network: none              # none | outbound | restricted
    timeout_seconds: 60
    memory_mb: 1024
    allow_paths:               # paths mounted RW into the sandbox
      - .
    deny_paths:                # paths *never* mounted, even if user adds them
      - ~/.ssh
      - ~/.aws
      - ~/.gnupg
  ```

**Exit criteria.**

- `bash_exec` runs in a sandbox by default on macOS and Linux.
- Sandbox failures (no Docker, no `sandbox-exec`) degrade to a
  loud-warning host fallback, not a crash, when `backend: auto` is
  set; explicit `backend: docker` fails fast.
- Escalation flow verified: user can approve a single host
  execution; the approval is not cached.
- Unit tests cover: command translation, mount allow/deny logic,
  timeout/memory enforcement (with a stub backend).
- Integration test: `bash_exec rm -rf /tmp/sandbox-test/*` cannot
  affect host paths.

**Why now.** Tool-loop extraction (Phase 3) is the right place to
plumb the sandbox in once. Doing this *after* Phase 3 means we
modify one tool path, not two.

---

### Phase 5 — Structured event stream (logs as a dataset)

Goal: every conceptual moment a future analyst would care about
emits a typed event to a JSONL stream alongside the human log.

**Design.**

- New `observability/emitter.py`:
  ```python
  class EventEmitter:
      def emit(self, event: Event) -> None: ...
      def child(self, **fields) -> "EventEmitter": ...   # propagates session_id, etc.
      def flush(self) -> None: ...
  ```
  Backends: `JSONLEmitter` (writes `_logs/{session_id}.events.jsonl`),
  `ORMEmitter` (writes to a new `event` table — best-effort, off the
  hot path), and a `MultiEmitter` that fans out.
- New `observability/redactor.py`: regex+rule-based PII/secret
  scrubber (API keys, common credential prefixes, `email@`,
  user-supplied paths under `~`). Always on for exported datasets;
  configurable for live runs.
- New `core/events.py` — typed events:
  - `SessionStarted`, `SessionEnded`
  - `TurnStarted`, `TurnEnded`
  - `StageStarted`, `StageFinished` (with status, duration_ms)
  - `LLMCalled` (provider, model, label, prompt_hash, response_hash,
    tokens_in, tokens_out, latency_ms — *not* raw text by default;
    raw text behind a flag)
  - `ToolCalled`, `ToolResult` (name, input redacted, result_size,
    duration_ms, sandbox_meta)
  - `GuardDecisionEvent`, `EscalationEvent`
  - `CouncilRoundEvent` (round_number, councillor_labels,
    decisions_summary)
  - `RoutingEvent` (mode, risk, workflow_hint)
  - `PlanCreated`, `PlanRevised`, `StepCompleted`, `StepFailed`
  - `ContextPacked` (token estimates, fidelity counts)
  - `ImportanceScored`
  - `SandboxRun` (backend, duration_ms, exit_code, network_policy)
  - `ErrorRaised` (component, error_type, message)
- `Container` carries one root `EventEmitter`. Stages and the tool
  loop accept an emitter; they emit events at well-defined points.
- The text logger is unchanged. Emitter is *additional*; if it
  fails, the run continues.
- `observability/loader.py`: pandas/parquet view over a
  session's events. Tabular API: `events_for(session_id)`,
  `tool_calls_for(session_id)`, `joined_session_summary(...)`.
- `observability/export.py`: take N session IDs, redact, emit a
  parquet dataset suitable for analysis or RL training. Per-event
  partitioning so analysts don't have to load everything.

**Schema versioning.** Every JSONL line has a `schema_version` field.
Loader handles old versions; export normalises to current.

**Privacy.**

- Default redaction is on for export; off for local logs (so the
  user can still debug their own runs).
- An explicit allowlist controls what raw fields enter the event:
  prompts and responses are off by default; tool outputs are
  size-only by default; user messages are redacted by default.
- Per-event "privacy class": public (never sensitive),
  user-content (redact for export), internal (always redact).

**Exit criteria.**

- `_logs/{session_id}.events.jsonl` produced for every run.
- Loader returns a dataframe of all events for a session.
- Exporter produces a parquet file from a directory of sessions
  with redaction applied; round-trip test verifies no raw API keys
  or user paths leak.
- Event schema documented in `observability/SCHEMA.md`.
- Unit tests for the redactor (a fixture of "should be scrubbed"
  vs "should pass").

**Why now.** Phases 3 and 4 are natural emission points; doing
events *after* them avoids churn but should not be deferred too
long because every future change is a chance to emit something
useful that we'll otherwise miss.

---

### Phase 6 — Plan vs PlanRun split

Goal: Plan/Step are the planner's output. PlanRun/StepRun carry
execution state. The two must not share a class.

**Scope.**

- `planning/schema.py` keeps `Plan`, `Step`, `ActionType`,
  `PLAN_JSON_SCHEMA`. Removes `StepStatus`, `result`, `error`,
  `flags.retry_count/deferred/skipped`.
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
- `EntityCriticStage` works on `Plan` (no execution state yet);
  its corrections produce a new `Plan` (immutable).
- Persistence layer (`runtime/persistence.py`) maps `PlanRun`/
  `StepRun` to the existing ORM tables. ORM already has this
  shape — no migration needed.
- Replan: produces a new `Plan` (spec) which is wrapped in a fresh
  `PlanRun`; both are persisted as separate rows.

**Exit criteria.**

- No mutation of `Plan.steps` outside `Plan.__init__`.
- Tests cover the spec/run conversion boundary.
- Replan produces a new `PlanRun`, not an in-place edit.

**Why this position.** It's a focused refactor that touches the
execution stage; doing it after Phase 3 (tool loop extracted) is
strictly easier.

---

### Phase 7 — Persistence consolidation onto the ORM

Goal: one persistence story. The on-disk artifact files stay; the
metadata about them moves to the ORM.

**Scope.**

- New ORM models in `db/models/`:
  - `Artifact` (already a placeholder — extend with kind, summary,
    source, created_at, last_accessed, access_count, decay_score,
    permanent, summary_embedding, project_tag).
  - `ConversationMessage` (session_id, turn, role, content).
  - `Request` (for workflow-discovery clustering — already separate
    semantically from conversation history).
  - `WorkflowCandidate` (status, frequency, recency_score,
    example_request_ids).
  - `Event` (Phase 5 — best-effort mirror; primary store stays JSONL).
- DAL methods for the operations the existing artifact store
  exposes: `set/get/meta/list/expel/expel_pattern/pin`,
  `record_request/discover_workflows`, `recall_sessions`,
  `recall_artifacts`, `apply_decay`, `set_active_project`.
- `runtime/artifact_store.py` becomes a façade over the DAL and
  the on-disk file management:
  ```python
  class ArtifactStore:
      def __init__(self, dal: ArtifactDAL, data_dir: Path,
                   inline_threshold: int, embedder: Embedder | None): ...
  ```
  No `sqlite3.connect` anywhere in this file.
- `_store/artifacts.db` is migrated into `data/agent.db` via an
  Alembic migration that imports rows verbatim. We keep
  `_store/data/*` as the on-disk artifact backing store.
- `sqlite-vec` becomes opt-in inside the DAL. Python cosine fallback
  remains available. When we move to Postgres later, swap to
  pgvector at the DAL layer with no caller changes.

**Exit criteria.**

- `artifact_store/` is a package with a thin façade and no raw SQL.
- An Alembic migration moves real data from `_store/artifacts.db`
  to `data/agent.db` without loss.
- All artifact-store consumers (tools, agent, RAG injection) pass
  unchanged.
- `_store/artifacts.db` is removed; the `_store/data/` files remain.

**Why this late.** It's the largest refactor and the one most
likely to introduce regressions. Doing it after the tests, the
container, the events, and the run-state split means we have
defence-in-depth when something breaks.

---

### Phase 8 — Cleanup pass and documentation

Goal: knock out the smaller items from 0051 §5–6 that aren't
load-bearing for the bigger phases.

**Scope.**

- Move scattered magic numbers (`_DIRECT_MAX_*`,
  `_MAX_RETRIES_PER_STAGE`, etc.) into `RuntimeConfig`. Document
  the *retry budget map* — one place explaining how stage retries,
  step retries, ask-user limits, monitor decisions, and
  direct-mode caps interact.
- Direct mode tool authorization: when arriving as the ABORT
  fallback from a high-risk classification, the
  DirectExecutionStage should be configurable to receive a
  reduced toolset.
- Logger module: extract palette state into a `LogFormatting`
  class; stop coupling `configure_logging` to metrics-writer
  initialisation.
- `runtime/utils.py` and `runtime/monitor.py` share an error-regex
  — extract `core/errors.py` as the canonical "what counts as a
  tool error" predicate.
- README/architecture docs reflect the new shape.

**Exit criteria.**

- Searching for `_MAX_` in `src/` returns only invariants, not
  tunables.
- A `docs/architecture.md` or top-of-`README.md` section explains
  the pipeline, container, sandbox, and event stream in one place.

---

## 6. Cross-cutting concerns

### 6.1 Bash sandboxing — backend selection logic

```
1. backend == "host"          → HostSandbox (warning event emitted)
2. backend == "docker"        → DockerSandbox (fails fast if docker absent)
3. backend == "sandbox-exec"  → MacSandboxExec (fails fast if not macOS)
4. backend == "auto":
     if docker available      → DockerSandbox
     elif on macOS            → MacSandboxExec
     else                     → HostSandbox + WARN once per session
```

Container start is amortised: one container per session, not per
call. Per-call execution uses `docker exec` against the running
container. Container is torn down on session end (and on KeyboardInterrupt).

The user-facing escalation language matters: "This command needs to
run on the host because it's installing system packages — approve?"
is more legible than the current "Allow [y/N]?". We carry the
sandbox decision into the `Escalation` payload.

### 6.2 Event stream — what we record vs what we redact

Recorded by default (always safe):

- Stage timings, decisions, error classes.
- Tool names, durations, exit codes, output sizes.
- LLM call metadata (model, label, token counts, latency, prompt
  hash, response hash).
- Council round summaries (verdict, agreement map).
- Sandbox metadata (backend, network policy, resource limits).

Recorded behind explicit opt-in:

- Raw prompts, raw responses, raw tool inputs, raw tool outputs,
  raw user messages.

Redacted on export regardless:

- API keys, auth tokens, common credential prefixes.
- Filesystem paths under `~/`, `/Users/<name>/`,
  `/home/<name>/`, network identifiers, email addresses.

This split lets a single user run with full local fidelity for
debugging while exports are safe to aggregate across users.

### 6.3 Test strategy

- **Unit tests (`_tests/unit/`)**: pure-logic targets. No
  fakes-with-state, no LLM, no DB. Synthesis math, packing,
  guard, validator, entity critic, workflows, redactor.
- **Integration tests (`_tests/integration/`)**: pipeline runs
  with a stub provider that returns canned responses, a stub
  sandbox that records calls, and a real (in-memory) artifact
  store. One test per stage transition pattern (RoutingStage→DONE,
  Planning→Critic→Strip→Execute, etc.).
- **End-to-end tests (`_tests/e2e/`)**: opt-in, real provider,
  small fixtures. Run on demand, not on every commit.
- **Coverage targets**: 80%+ for `core/`, `runtime/critic.py`,
  `runtime/context_manager.py`, `runtime/guard.py`,
  `runtime/validator.py`, `observability/redactor.py`. Lower
  expectations for stages and tool implementations.

### 6.4 Backwards compatibility

- Phase 4 (sandbox) and Phase 5 (events) are feature-flagged on
  rollout. The flag defaults to "on" once the phase ships and tests
  are green. No multi-release deprecation cycles.
- Phase 7 (artifact store cutover) requires a one-shot data
  migration. Until that runs, the agent reads from both stores and
  prefers the new one; the old store is removed in a follow-up.

### 6.5 Telemetry → analysis dataset → future learning

The same event stream serves three audiences in increasing order
of sophistication:

1. **You debugging a single session.** Today: tail the text log.
   After Phase 5: query the JSONL/parquet for "all tool calls in
   this session that took >1s".
2. **You analysing trends across sessions.** After Phase 5:
   loader produces a dataframe; you ask "which stages ABORT most
   often?", "what's the council survival rate by risk?", "how
   often does the planner produce an invalid plan on first try?".
3. **A learned policy or evaluator.** Later (Project 10/11
   spirit): exported parquet becomes training data. Imitation
   learning targets are step-level decisions; reward signals come
   from explicit user escalation outcomes (approved/denied) and
   implicit signals (did the user retry the same query?).

Phases 4 and 5 are the foundation; Phase 8's cleanup makes the
analysis path ergonomic. We do not need to commit to a learning
algorithm now — we just need the data shape to support multiple
future ones.

---

## 7. Sequencing summary

```
Phase 0  Cleanup + tests              ─┐
Phase 1  Container, kill singletons    │  must be in order
Phase 2  JSON contracts                │
Phase 3  Tool loop extraction          ┘
Phase 4  Bash sandboxing               ┐  Phase 4 and 5 can run
Phase 5  Structured events             ┘  in parallel after Phase 3
Phase 6  Plan vs PlanRun
Phase 7  Persistence onto ORM
Phase 8  Cleanup + docs
```

Phase 0 → 3 are sequentially required. Phases 4 and 5 are independent
and can be parallelised between contributors (or interleaved if
solo). Phase 6 → 7 are sequential because the run-state split makes
the persistence migration cleaner. Phase 8 closes out and
documents.

Rough effort sizing (XS = afternoon, S = day, M = 2–4 days, L = week+):

| Phase | Size | Rationale                                                        |
|-------|------|------------------------------------------------------------------|
| 0     | M    | Test infrastructure + first wave of unit tests.                  |
| 1     | M    | Touches every stage but each touch is small.                     |
| 2     | S    | One module + Anthropic structured-output trick.                  |
| 3     | M    | Extracted carefully with parity tests; large code move.          |
| 4     | L    | Real cross-platform work, escalation flow, integration tests.    |
| 5     | L    | Schema design + emitter + loader + redactor + export.            |
| 6     | S    | Mostly mechanical given Phase 3 already cleaned execution.       |
| 7     | L    | Data migration + DAL design + façade rewrite.                    |
| 8     | S    | Polish.                                                          |

---

## 8. What we're explicitly *not* changing

- The pipeline + stage + StageResult contract. It works.
- The council deliberation primitive. It works and has the right
  abstraction (`DeliberationAdapter[T]`). We just give it tests
  and emit events from it.
- The toolset-owned routing rules pattern. We keep it; we may
  extend `planning_note` into a structured form later.
- The text log format. Add events alongside; don't replace.
- The provider interface. Phase 2 extends it (structured output)
  but doesn't replace it.

---

## 9. Open decisions

1. **`core/` import path.** Move modules now (Phase 1) or after
   each subsystem stabilises? Recommendation: move during the
   relevant phase, not all upfront — the renames are noisy and
   reviewable in context.
2. **Docker as a hard dependency on Linux.** If the user has no
   Docker, fall back to bubblewrap or fail? My default is
   "auto-detect, warn loudly, fall through to host" because the
   alternative is users disabling sandbox entirely and we lose
   the audit signal.
3. **Where does the JSONL events file live?** Today text logs
   live in `_logs/`. Co-locating events as `_logs/{sid}.events.jsonl`
   is simplest; `_metrics/` is already there and might be a better
   fit. Recommendation: `_logs/{sid}.events.jsonl` — one
   directory per session-related artifact.
4. **Multi-user analytics path.** When other users start running
   this, do exported datasets go anywhere automatic, or strictly
   user-pulled? Recommendation: strictly user-pulled. We design
   for opt-in upload but don't build the upload service in this
   refactor.
5. **Anthropic structured output.** "Single tool" trick vs the
   newer `tool_choice="any"` + JSON tool patterns. Recommendation:
   start with the single-tool trick because it works on every
   Anthropic model we support; revisit if Anthropic ships a
   first-class JSON-mode equivalent.

---

## 10. What success looks like

- I can run two `Agent` instances side-by-side in a single test
  with different configs (Phase 1).
- I can `pytest _tests/` and watch >80% coverage of pure logic
  (Phases 0, 6).
- A malicious `bash_exec rm -rf /` can't escape the project tree
  (Phase 4).
- I can run `python scripts/export_dataset.py --since 30d` and get
  a redacted parquet file I'd be comfortable sharing (Phase 5).
- A future analysis question — "which stages cost the most
  tokens?", "did the council ever overturn a workflow plan?",
  "how often does the entity critic correct a real path vs a
  prose phrase?" — is answerable in pandas without re-instrumenting
  the agent (Phase 5).
- The artifact store has one persistence story, queryable with the
  same DAL as everything else (Phase 7).
- The `_projects/` directory and the dead `IntentClassifier` are
  gone (Phases 0, 2).
- Text logs are still beautiful (every phase).
