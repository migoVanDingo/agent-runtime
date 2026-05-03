# 0051 — Architecture and Pattern Review

> Independent review of the agent-runtime codebase as it stands today
> (post-0049 ORM/DAL phase, post-0040 pipeline cutover). Generated
> without consulting the design docs in `_plans/` so observations
> reflect what the *code* says, not what was intended.

## 1. What this project is

A Python ReAct-style agent runtime that turns a free-form user message
into either an inline answer or an executed multi-step plan. It sits
on top of a multi-provider LLM abstraction and a pluggable toolset
catalog, and layers on a runtime-infrastructure stack inspired by
the AFM and VIGIL papers — context compression, plan validation,
adversarial review, execution monitoring, safety guard, and
session-scoped artifact memory.

The CLI entry is `src/main.py`; the orchestrator is `src/agent.py`.
There are 179 Python source files, but the architectural surface is
much smaller than that count suggests because most files are tool
implementations (≈70) and pipeline/runtime components (≈30) sit on a
handful of stable abstractions.

---

## 2. Architecture at a glance

```
┌─────────────────────────────────────────────────────────────────┐
│                         main.py (CLI)                           │
│   - session id resolution (new vs --resume)                     │
│   - artifact_store init, decay sweep, workflow-candidate prompt │
│   - REPL loop → agent.call(user_message)                        │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                  ┌─────────────▼──────────────┐
                  │         agent.py           │
                  │  Wires every dependency,   │
                  │  builds Pipeline, holds    │
                  │  Messenger (history) and   │
                  │  ContextManager.           │
                  └─────────────┬──────────────┘
                                │
        ┌───────────────────────▼───────────────────────────────┐
        │                Pipeline (runtime/pipeline.py)         │
        │  Ordered list of Stages with explicit transition      │
        │  semantics: OK / DONE / RETRY / ASK_USER / ABORT.     │
        │  Single fallback stage on any ABORT.                  │
        └───┬───────────────────────────────────────────────────┘
            │
            ▼ (each stage reads/writes shared PipelineContext)
   1. RoutingStage           ← single LLM call: classify + maybe answer
   2. DirectInlineStage      ← short-circuit clean conversational answers
   3. WorkflowMatchStage     ← classifier-hint → regex → fallback LLM
   4. PlanningStage          ← LLM planner + validator (RETRY loop)
   5. EntityCriticStage      ← scrub hallucinated paths/filenames
   6. ValidatorStage         ← log plan, ABORT if None
   7. CouncilStage           ← N-agent adversarial critique + revise/strip
   8. ExecutionStage         ← per-step: tool→guard→execute→monitor→decide
   9. SynthesizerStage       ← write final answer (when requires_synthesis)
  10. DirectExecutionStage   ← also fallback: free-form tool loop
```

### 2.1 Core building blocks

| Layer            | Module                              | Responsibility                                                          |
|------------------|-------------------------------------|-------------------------------------------------------------------------|
| Provider         | `providers/{anthropic,openai_compat,...}` | Normalised `chat()` over Anthropic + 5 OpenAI-compatible providers. |
| Tooling          | `tools/base.py`, `tools/registry.py`, `tools/toolsets.py` | `BaseTool` interface, registry, eleven curated toolsets.       |
| Routing          | `routing/static_router.py`          | Heuristic rules + embedding similarity → toolset list.                  |
| Workflows        | `workflows/`                        | Hand-written `Workflow` templates that emit a Plan deterministically.   |
| Planning         | `planning/{planner,synthesizer,prompts}` | LLM-backed plan/replan/revise/synthesize.                          |
| Pipeline         | `runtime/pipeline.py` + `runtime/stages/*.py` | One file per stage, sharing a typed `PipelineContext`.        |
| Runtime services | `runtime/{validator,critic,council,monitor,guard,context_manager,importance,entity_critic,escalation}` | The "agent infrastructure" layer. |
| Memory           | `runtime/artifact_store.py` + `embeddings.py` | SQLite-backed named artifact registry, decay, RAG recall.       |
| Persistence      | `db/`, `runtime/persistence.py`     | SQLModel/Alembic structured DB for sessions/plans/steps/artifacts.      |
| Config           | `config.py`, `settings.py`, `app_config.py` | Two-track: YAML for runtime tuning, `BaseSettings` for env/secrets. |

### 2.2 Two memory systems, two purposes

- **`runtime/artifact_store.py`** is the *operational* memory used by
  the agent to remember tool outputs, web pages, dataframes, and
  previous sessions. It owns its own SQLite DB at `_store/artifacts.db`,
  speaks in `ArtifactMeta` objects, supports inline-vs-file-backed
  storage, decay scoring, and embedding-based recall.
- **`db/`** is the *introspection* memory: an ORM-modelled relational
  schema (SQLModel + Alembic + async SQLAlchemy) that persists
  every session/plan/step/artifact for offline analysis. It is
  feature-flagged via `ENABLE_SESSION_PERSISTENCE` and gated through
  a single `PersistenceWriter` façade.

Both stores can be active simultaneously and they do not currently
share IDs — the artifact store has its own `session_id`, the ORM has
its own ULID-prefixed `sess_…`.

---

## 3. Dataflow walkthrough

A single `agent.call(user_message)` invocation:

1. **REPL → Agent.call.** The user line is appended to `Messenger`,
   spinner starts, optional RAG block from prior sessions/artifacts is
   prepended on the first turn, and `PersistenceWriter.start_session`
   creates an `agent_session` row.
2. **Pipeline.run(context).** The runner iterates stages in order.
   Each stage receives the shared `PipelineContext`, mutates only its
   own slice of fields, and returns a `StageResult` whose status drives
   the runner.
3. **RoutingStage.** One combined LLM call produces:
   - a `<route>{...}</route>` JSON header → `ClassifierResult(mode, risk, workflow_hint)`,
   - and any prose after the header, captured as `answer_text`.
   `ContextManager.pack()` is called here for the first time with the
   raw conversation. The packed view is reused by EntityCritic and the
   first execution step.
4. **DirectInlineStage.** If `mode=direct` and `answer_text` looks
   conversational (no code fences, no "let me…"-style action phrases),
   the stage records the answer in the messenger and returns DONE.
   Otherwise it falls through.
5. **WorkflowMatchStage** (only when `mode=plan`). Tries:
   1. classifier hint → `Workflow.try_match` → `generate_plan` direct,
   2. regex match across all `Workflow.pattern`s,
   3. targeted LLM fallback (`WorkflowSelector`).
   Either produces a deterministic `Plan` or leaves it `None`.
6. **PlanningStage.** If `plan is None`, calls the full LLM planner
   with `PLAN_JSON_SCHEMA` (OpenAI structured output) and validates
   structurally with `PlanValidator`. Invalid plans drive a RETRY (max 2)
   with the validator feedback appended to `user_message`.
7. **EntityCriticStage.** Scans plan step descriptions for
   path-like tokens not present in `entity_context` (the text-only
   slice of packed messages) and rewrites them to nearest neighbours
   from history. Suspicious corrections (no slash, no extension) are
   reverted and surfaced via `ASK_USER`.
8. **ValidatorStage.** Pure logging gate — ABORTs if `plan is None`.
9. **CouncilStage.** Workflow-generated plans bypass entirely. Plans
   from the LLM go through a configurable N-agent council
   (`runtime/council.py` + `runtime/critic.py`):
   - Per-risk scaling: `low=0`, `moderate=1`, `high=N`.
   - Each councillor independently reviews via `ThreadPoolExecutor`.
   - `PlanCriticAdapter.synthesize` aggregates verdicts using a
     consensus threshold and a downgrade ladder (drop → replace → justify → discard).
   - Surviving challenges are sent to `Planner.revise`; revisions that
     fail validation cause the challenged steps to be stripped instead.
10. **ExecutionStage.** Walks the plan with retry/replan/defer/skip/escalate
    semantics. For each step:
    - `_step_system` builds a per-step system prompt with the
      step-progress checklist and a tool-specific note.
    - `ContextManager.pack` re-runs every iteration, passing
      `plan_start_index` so plan-window messages are protected.
    - Single tool (or step.tool + utility) handed to the model;
      unauthorised tool calls are rejected.
    - `ActionGuard` BLOCKs/ESCALATEs based on regex patterns, with an
      approval cache keyed by tool + canonicalised input.
    - `read_url` is wired to a prompt-injection quarantine path.
    - Repeat-tool-call detection forces wrap-up; max-tokens "patches"
      dangling `tool_use` blocks with synthetic `tool_result`s to keep
      the message history valid.
    - `ExecutionMonitor` triages results heuristically and only invokes
      an LLM when something is flagged.
    - `ImportanceScorer` writes back into `ContextManager._importance_overrides`
      so subsequent packs see real importance for tool results.
    - Each step is mirrored to the ORM via `PersistenceWriter.record_step`.
11. **SynthesizerStage.** Only runs if `plan.requires_synthesis=True`;
    summarises completed step results into a coherent final answer and
    returns DONE so the fallback is never reached.
12. **DirectExecutionStage.** Reached either as the direct-mode tool
    loop or as the ABORT fallback. Free-form ReAct loop with its own
    iteration cap, error-correction injection, repeat detection,
    truncation, and prompt-injection quarantine.
13. **Agent.call wrap-up.** `PersistenceWriter.finish_session`,
    `ArtifactStore.record_request` (for workflow-discovery clustering),
    spinner stop, return.

---

## 4. The Good — patterns worth keeping and extending

### 4.1 Stage-as-pure-function pipeline
`Pipeline` + `Stage` + `StageResult` + `PipelineContext` is the spine
of the codebase, and it is genuinely well-shaped:

- Stages declare a name, read a documented slice of context, write a
  documented slice, and return a `StageStatus`.
- The status enum is small and total: `OK | DONE | RETRY | ASK_USER | ABORT`.
- The runner owns retry/ask counts and the fallback. Stages don't
  manage cross-stage flow; they manage "what should happen next?".
- ABORT-to-fallback gives every error path a defined recovery instead
  of crashing the REPL turn.

This is the strongest pattern in the project. The contract on
`Stage` (see `runtime/stage_base.py:7-34`) is exactly the kind of
crisp interface that makes new components cheap to add.

**How to extend**

- New runtime checks (e.g. cost guardrails, rate-limit pause, policy
  audit) are one-stage drops into the pipeline list.
- The pipeline is a great host for *trace replay*: because every stage
  reads/writes a typed context, a recorded `(input_context,
  result)` log is enough to deterministically replay any past run
  against new code (Project 5 in the curriculum).
- Once `db/` persistence is mature, we can persist `PipelineContext`
  diffs per stage to power "why did the agent do that?" UIs.
- The same shape can host *speculative* execution: run two competing
  plans through duplicated stages and pick the winner.

### 4.2 Provider abstraction with shared OpenAI translator
`providers/base.py` defines `BaseProvider`, `TextBlock`, `ToolUseBlock`,
`TokenUsage`, `ProviderResponse`. Five OpenAI-compatible providers
(OpenAI, Ollama, Grok, DeepSeek, Gemini) all extend
`OpenAICompatibleProvider`, which centralises:

- Anthropic↔OpenAI message translation,
- tool schema translation,
- tool-call ↔ tool-result wiring,
- retry-on-rate-limit,
- token tracking.

Each concrete provider is essentially nine lines of `__init__`. That
is exactly the cost we want for adding a new vendor.

**How to extend**

- A streaming variant could be added as `BaseProvider.chat_stream` and
  implemented once per family; the rest of the system already buffers
  full responses, so making it opt-in is trivial.
- Prompt caching for Anthropic should hang off this abstraction — see
  §5 ("Bad").
- A Gemini-native provider (rather than OpenAI-compat) would unlock
  thinking budgets and structured citations and would slot in the same
  way the Anthropic provider does today.

### 4.3 Toolset-owned routing rules
`tools/toolsets.py` bundles each toolset with its own `RoutingRule`s
(keyword, extension, regex, last-tool-was). `StaticRouter` then
combines those rules with cosine-similarity over toolset description
embeddings. Defaults are explicit in `config.routing.default_toolsets`.

This co-locates the "when do you use me?" answer with the toolset
itself instead of a global router file. The result is that adding
a toolset means adding a directory, registering it once in
`ALL_TOOLSETS`, and the router automatically knows when to surface it.

**How to extend**

- This pattern would graduate cleanly into a plugin system: each
  toolset becomes its own package with `tools/`, `rules`, and an
  optional `planning_note` and `description`. `ToolRegistry` already
  treats them as a collection.
- The `planning_note` field is currently free-form text shown in the
  planner prompt. Promoting it to a structured "tool-selection cheat
  sheet" surfaced in the council prompt would let critics challenge
  tool choices with concrete ground truth instead of trained priors.

### 4.4 Council deliberation primitive
`runtime/council.py` is an under-recognised gem. The
`Council` + `DeliberationAdapter[T]` separation cleanly splits the
domain-agnostic deliberation harness (parallel queries, debate rounds,
convergence early exit, run metrics) from domain-specific decoding
and synthesis (`PlanCriticAdapter` in `runtime/critic.py`).

The synthesis algorithm is the more interesting half: votes are
ratio-thresholded (`runtime/critic.py:132-227`), lone-wolf challenges
get downgraded to "justify" rather than discarded, and N=2 ties are
preserved as genuine 50/50 splits. This is real consensus
engineering, not majority vote.

**How to extend**

- The same harness can host plan-revision councils, synthesis-quality
  councils, validator councils, or even tool-selection councils —
  any decision where ensembling reduces variance.
- `consensus_threshold` and `dynamic_scaling` are tunable per-context;
  the next step would be a learned scaling policy (see Project 11).
- `_metrics/` records make this the primary lens for offline
  evaluation: you can ask "did the council change the outcome?",
  "which councillor is the loneliest wolf?", "what's the marginal value
  of the third councillor?".

### 4.5 Non-destructive context manager (AFM-shaped)
`ContextManager` (`runtime/context_manager.py`) keeps the full
conversation in `Messenger` and produces a budget-shaped *view* per
provider call. Score = importance × similarity × recency-decay; fidelity
ladders FULL → COMPRESSED → PLACEHOLDER; tool_use/tool_result pairs are
treated as atomic for both scoring and packing (so you never end up
with an orphaned `tool_use_id`); plan-window messages are floored at
COMPRESSED (FULL for tool results) so the model never loses the data
it needs to make the next step. LLM summarisation is opt-in with a
content-hash cache.

The pair-atomicity in `_pack_chronological`
(`runtime/context_manager.py:267-353`) is the kind of detail that
saves entire categories of API errors and is genuinely correct.

**How to extend**

- Persist `_importance_overrides` to the artifact store keyed by
  (session_id, message_index) so resumed sessions keep prior LLM
  judgements instead of starting cold.
- Replace the per-call cosine pass with a vector index when the
  conversation gets long enough that the O(n) embed pass dominates.
- Move summarisation from sync-blocking to background pre-warm: the
  cache is content-hashed so a worker thread can speculatively
  summarise large tool outputs the moment they land.

### 4.6 Heuristic-first, LLM-second monitor
`ExecutionMonitor.assess` runs cheap regex heuristics first
(`_TOOL_ERROR_RE`, empty-result, step.error) and only spends an LLM
call when something is flagged. "command not found" short-circuits
straight to REPLAN without calling the model at all. Low-confidence
RETRY decisions are downgraded to SKIP. This is good defence-in-depth
that respects token cost.

**How to extend**

- Same pattern can replace the LLM call in `ImportanceScorer` for
  obvious cases (e.g. write_file confirmations, empty results).
- The heuristic regex is already shared with `runtime/utils.py`. A
  proper `errors.py` module owning the canonical "what counts as a
  tool error" predicate would let the guard, monitor, executor, and
  context manager agree without duplicating the regex.

### 4.7 Authorization gate inside the step loop
`ExecutionStage._run_step` builds `_authorized_tools` from the tools
it hands the model, and rejects any tool call outside that set with
a `tool_result` describing the rejection (`runtime/stages/execution.py:428-438`).
This is structurally stronger than just trusting the model not to
hallucinate a tool — and it's enforced at the same layer that gates
on the safety guard.

**How to extend**

- Generalise to per-step *path* authorization: declare in the plan
  that this step may only touch `_store/` or only fetch a single URL,
  enforce at execute time. This already aligns with `step.produces`,
  which is currently advisory.
- Wire `step.produces` from advisory to enforced: missing artifacts
  could be a RETRY signal instead of just a log warning.

### 4.8 Two-track configuration
`config.yml` (typed dataclasses in `config.py`) for runtime tuning vs
`Settings` (`pydantic-settings`) for environment/secrets. The split is
clean: secrets in `.env`, behavioural knobs in YAML, both surfaced
through `app_config` with `lru_cache` accessors.

**How to extend**

- The dataclasses are currently parsed by hand
  (`config.py:185-277`). Promoting them to pydantic models would give
  validation and env-overrides for free, while keeping the YAML
  file as the source of truth.

### 4.9 Workflow templates — pre-shaped plans
`workflows/` is a small but powerful idea: hand-written `Workflow`
classes whose `pattern` regex + `generate_plan` deterministically
produce a `Plan`. The pipeline already gives them three matching
paths (classifier hint, regex, fallback selector) and lets them
bypass the council entirely because they're not LLM-hallucinated.

**How to extend**

- Couple this to the artifact-store *workflow discovery* pipeline
  (`runtime/artifact_store.discover_workflows`): when a candidate is
  approved, scaffold a `Workflow` subclass under
  `workflows/implementations/` so it becomes deterministic on the
  next run.
- Template parameters (regex group names) could become typed.

### 4.10 Artifact store with three tiers in one file
The store's three tiers (CRUD → resumption/conversation/decay/discovery
→ embeddings/RAG/projects) live in one well-commented file. The
sqlite-vec backend probe with python-cosine fallback
(`runtime/artifact_store.py:308-335`) and the inline-vs-disk dispatch
keyed on `inline_threshold` are both pragmatic.

---

## 5. The Bad — patterns that hurt today and need fixing

### 5.1 Singletons everywhere
- `app_config.config` and `app_config.settings` are *module-level*
  cached objects — every importer touches them at import time.
- `runtime.artifact_store._store` is a module-level singleton initialized
  by `init_store`; access through `get_artifact_store()` raises if not
  initialized first.
- `embeddings._model` is a module-level lazy-loaded singleton.
- `runtime.council_metrics.get_metrics_writer()` — same shape.
- `db.engine._{agent,briefbot}_engine` — same shape.

**Best practice.** Inject dependencies at the edges; resolve the
singleton once in `main.py` and pass the object through. The
`Agent` class already does this internally — it just then depends on
imports that re-resolve singletons (e.g. `ExecutionStage` does
`from runtime.artifact_store import get_artifact_store` from inside a
method).

**Gap.** Tests and reuse are hard. Want a different artifact store
per test? Patch the module global. Want to run two pipelines side by
side (e.g. ultrareview-style)? You can't. Worse, `app_config.config`
is read at *import time* in 30+ files, so changing config requires a
process restart. The `lru_cache(maxsize=1)` indirection makes this
look like dependency injection but is not.

**Improvement path.**
1. Stop reading `config` at import time in stage modules. Instead,
   pass the relevant config slice through the stage's constructor.
   This already works for many stages.
2. Replace `get_artifact_store()` calls inside stages with a
   constructor-injected `artifact_store: ArtifactStore | None`.
3. Add a `class AgentRuntime` or `class Container` that owns
   `provider`, `runtime_provider`, `registry`, `artifact_store`,
   `db_engine`, etc., and pass it to `Agent(...)`. `main.py` builds
   the container; tests build a fake one.

### 5.2 Inline `import` inside hot loops
Throughout `runtime/stages/execution.py`, `agent.py`, and
`runtime/artifact_store.py` you see things like:

```python
from runtime.persistence import PersistenceWriter
from runtime.artifact_store import get_artifact_store
from messenger import Messenger
import re as _re
```

inside `for`/`while` loops or in deeply-nested branches. There are at
least 25 of these.

**Best practice.** Imports go to the top of the file; circular
dependencies are resolved with `from __future__ import annotations` +
`if TYPE_CHECKING:`, or by extracting an interface module.

**Gap.** Today most of these inline imports are not breaking anything,
but they are a smell of unresolved dependency cycles between
`runtime.*`, `tools.*`, and the artifact store. A few are real cycle
breakers (e.g. `ContextManager._compress_tool_result` importing
`Messenger` to talk to the summariser provider).

**Improvement path.**
- Replace `Messenger`-as-payload with `list[dict]` directly when only
  the wire format matters (the summariser path is one example).
- Promote `runtime/persistence.py` and `runtime/artifact_store.py`
  to top-level imports — they don't transitively import any stage
  code, so there's no real cycle.
- For genuine cycles (logger ↔ council_metrics ↔ logger), invert
  the dependency: have the metrics writer accept a logger, not import
  one.

### 5.3 Inconsistent error handling
There are at least three styles:

- "Silent and swallowing": `try: ... except Exception: pass` in
  `runtime/artifact_store.py:622-623`, `agent.py:233-235`,
  `runtime/persistence.py` (every method).
- "Log warning and return None":
  `runtime/persistence._safe_chat`, `Planner._safe_chat`,
  many places in the artifact store.
- "Log and raise": rare but happens
  (`providers/anthropic.py` rate-limit handler, eventually re-raises).

**Best practice.** Errors should be classified at the boundary they
cross. A *recoverable* failure (provider transient error, bad JSON
from the model, missing optional config) should be logged and a typed
result returned. An *unrecoverable* failure (programming error, bad
state) should propagate.

**Gap.** The current code mixes both at every layer:
`artifact_store.set` swallows the persistence write
(`runtime/artifact_store.py:622`), but the same kind of error in
`PersistenceWriter.start_session` returns None which the caller
silently uses. Debugging "why isn't this artifact in the DB?" means
reading every except-handler in the chain.

**Improvement path.**
- Define `class RuntimeError(Exception)` hierarchies in `runtime/`
  for each layer (PersistenceError, ArtifactStoreError, ProviderError).
- Stage contracts already promise to return RETRY/ABORT instead of
  raising for *recoverable* failures — extend that contract one layer
  down: persistence should raise on programming errors and return
  `Optional[T]` for "feature is disabled / external dep is missing".
- Stop catching bare `Exception` in non-pipeline code unless you're
  *prepared to attribute the failure*. A logged warning that doesn't
  name what was suppressed is worse than a stack trace.

### 5.4 LLM JSON parsing fragility
Three different LLM-output parsers:

- `runtime.utils.parse_routing_response` — regex `<route>...</route>` +
  json.
- `runtime.critic._extract_json` — fenced code block, then bare
  `{...}` walked from each `}` backwards to handle trailing prose.
- `Planner._parse` — strip ``` fences, json, validate keys.
- `runtime.monitor._parse` — strip ``` fences, json.
- `runtime.classifier._parse` — strip ``` fences, json.
- `runtime.importance._parse` — strip ``` fences, json.

Each one re-implements 10–30 lines of "tolerate-ish JSON".

**Best practice.** Use the provider's structured-output / tool-use
mode. The OpenAI-compat provider already supports `json_schema` (set
in `Planner.plan` via `PLAN_JSON_SCHEMA`). Anthropic supports tool-use
JSON or `response_format`-style instructions.

**Gap.** Only `Planner` uses structured output. The critic, monitor,
classifier, importance scorer, and routing header all rely on
"please return JSON" + tolerant parsing. Anthropic's path doesn't
even pass `json_schema` through (`providers/anthropic.py:16-58` simply
ignores the `json_schema` arg).

**Improvement path.**
- Promote `_extract_json` to `runtime/json_extract.py` and use it
  everywhere as a fallback only.
- Add an Anthropic structured-output path: tool-use with a single
  "respond" tool whose schema is the expected output. This is the
  same pattern OpenAI's `response_format=json_schema` solves, and is
  the recommended Claude-API approach.
- For each consumer, declare the expected schema once, share it.

### 5.5 Tool authorization at the wrong level (per-step only)
The "you may only use the tools I gave you" check exists in
`ExecutionStage._run_step:428-438` but not in
`DirectExecutionStage._run_loop`. In direct mode the model can call
*any registered tool*, and the only thing that stops it is `ActionGuard`'s
pre-execution regex.

**Gap.** Direct mode is the fallback path for every ABORT, so
"plan failed → free-for-all" is the current behaviour. If the planner
correctly recognised a request as too risky for an automatic plan,
the fallback hands the model a strictly larger toolbox.

**Improvement path.**
- Pass the routing-time toolset list into DirectExecutionStage instead
  of always calling `StaticRouter.select` again.
- Optionally add a "high-risk fallback" config that, on ABORT from a
  high-risk classification, restricts the fallback to a read-only
  toolset.

### 5.6 Missing tests
There is a `_tests/` directory at the project root, but it is empty
(`ls _tests/` shows two subdirs, both empty in this snapshot). There
are no automated tests for:

- the council synthesis math (`runtime/critic.py:132-227`),
- `_pack_chronological` pair atomicity,
- workflow regex matchers,
- `ActionGuard` patterns,
- the entity critic's suspicious-correction reverter.

**Best practice.** All five of those are *pure-logic, no-IO* code
paths. They should each have ~20 lines of pytest cases. The
council-synthesis algorithm has at least eight branches that visibly
disagree about edge cases (`k > 1`, `k == 1`, `N == 2`, etc.); without
tests, a refactor is a coin flip.

**Improvement path.**
- Start with `runtime/critic.py::PlanCriticAdapter.synthesize` — it's
  pure data in / data out with all the branches in one function.
- `routing/static_router.py` heuristics are likewise easy to fixture.
- Workflow regex tests guard against the "regex + classifier hint"
  drift that already happened with `ReadModifyWrite`.

### 5.7 SQLite is the only backend, but the abstraction promises portability
`db/engine.py:8` claims "Switching from SQLite to Postgres requires
only a settings change", and the SQLAlchemy + SQLModel stack supports
that. But `runtime/artifact_store.py` is hand-written SQLite:
`sqlite3.connect`, raw `INSERT OR REPLACE`, `BLOB` for embeddings, and
optional `sqlite-vec` extension loading. Switching that to Postgres
(or even Postgres + pgvector) is a rewrite, not a settings change.

**Gap.** Two persistence layers, one sqlmodel-portable, one
sqlite-coupled. They overlap conceptually (artifact metadata,
sessions, request logs).

**Improvement path.**
- Long-term: collapse the two stores into one. `db/` has the model
  framework; `artifact_store` has the working features. Move artifact
  CRUD onto SQLModel, keep the on-disk artifact files as-is, lose the
  raw SQL. This is a substantial refactor and probably belongs in its
  own roadmap entry, but it removes the divergence cost.
- Short-term: stop adding features to `artifact_store.py` that
  duplicate `db/` (request logging is the most obvious overlap).

---

## 6. The Ugly — patterns that work but smell, in approximate severity order

### 6.1 `_run_step` and `_run_loop` are 150+ line procedural blobs
`runtime/stages/execution.py:368-545` and
`runtime/stages/direct_execution.py:76-293` are nearly identical
ReAct loops with subtle differences: tool authorization, error
correction injection, max-iteration vs max-tool-calls, prompt-injection
quarantine appears in both with copy-pasted blocks.

**Best practice.** Extract the shared loop into one function with
hooks: `pre_tool_call(block)`, `post_tool_call(block, result)`,
`should_force_end(state)`. The two callers differ only in those hooks.

**Gap.** Bug fixes have to be made twice. The injection-quarantine
block (≈40 lines) is duplicated verbatim. The "repeat tool sig
detection" is duplicated. The max-tokens dangling-tool patch is
duplicated. Future provider features (caching, thinking) will need
to be added in both places.

**Improvement path.**
- Define `class ToolLoop` in `runtime/tool_loop.py` parameterised by
  iteration cap, tool-call cap, error-correction policy, and
  authorization predicate. Both stages compose it.
- Move the prompt-injection quarantine into a small helper
  `runtime/injection_gate.py` that both call.

### 6.2 Sync-over-async via `run_async`
`db/sync.py` runs every async DAL call by *creating a fresh asyncio
event loop per call*. That's correct in isolation but extremely
expensive: every `PersistenceWriter.record_step` allocates a loop,
runs to completion, and tears it down. ExecutionStage calls this
once per step, and the artifact store calls it on every `set()`.

**Best practice.** When a sync codebase needs async DAL, hold a
single long-lived loop in a background thread and dispatch with
`asyncio.run_coroutine_threadsafe`. Or — and probably better here —
make the persistence layer batch and asynchronous-but-buffered, since
nothing in the hot path actually needs the persisted ID immediately.

**Gap.** Today persistence adds a measurable per-step latency hit
(loop construction, engine pool acquisition, transaction commit). If
the sync bridge runs at scale this becomes the slowest line in the
agent.

**Improvement path.**
- Phase 1: cache the engine + sessionmaker globally (already
  partially done) and run all DAL through a single shared loop.
- Phase 2: move persistence behind an in-memory queue and a
  background drainer thread — all callers become non-blocking
  enqueues.
- Phase 3: when the agent moves async (when?), tools can `await`
  directly and `db/sync.py` is deleted.

### 6.3 `runtime/artifact_store.py` is 1278 lines
One file holds: schema DDL, dataclasses, helpers, the store class,
inline summary functions, embedding helpers, blob (de)serialisation,
workflow discovery clustering, decay sweep, RAG recall, project
scoping, request logging, candidate approval, and a module-level
singleton. The class has 50+ public methods.

**Best practice.** Single-responsibility split per "tier" and per
concern: `artifact_store/core.py` (CRUD), `artifact_store/recall.py`
(RAG), `artifact_store/discovery.py` (clustering), `artifact_store/decay.py`
(sweep).

**Gap.** Code review of this file is hard. Every change touches the
whole module. The pure functions (`_cosine_similarity`, `_summary_for_text`,
`_serialize`, `_deserialize`, `_vec_to_blob`, `_blob_to_vec`) are
trivially extractable.

**Improvement path.** Mechanical split first; behavioural change
later. The dataclasses, helpers, and DDL constants can be moved out
in an afternoon with no semantic change.

### 6.4 Plan model carries execution state
`planning/schema.py::Plan` and `Step` mix planner output (description,
action_type, tool, produces, flags) with execution state (status,
result, error, retry_count, deferred, skipped). The same dataclass
that the planner emits is mutated in place during execution.

**Best practice.** Two types: `Plan`/`Step` for the spec, and
`PlanRun`/`StepRun` for the execution log. The pipeline already has a
`PlanState` analogue in the form of completed/queue indices in
`ExecutionStage._execute_plan`; persisting that as a real type
removes the in-place mutation.

**Gap.** `Step.flags.retry_count` is a runtime counter that gets
serialised back to the planner via `Step.to_dict`. Replan and revise
both have to be careful not to leak completed-step state. The ORM
schema (`db/models/plan.py`) implicitly admits this by separating
`Plan` (spec) from `Step` (state) into two tables.

**Improvement path.**
- Promote `StepStatus`, `result`, `error`, runtime counters out of
  `planning.schema` and into a runtime-side `StepRun` dataclass.
- Plan→PlanRun conversion at execute-time, not in-place mutation.

### 6.5 Magic numbers and config shadows
Constants scattered through:

- `runtime/stages/direct_execution.py:25-28`:
  `_DIRECT_MAX_TOOL_RESULT_CHARS = 50_000`,
  `_DIRECT_MAX_TOOL_CALLS = 15`, etc.
- `runtime/pipeline.py:11-15`: `_MAX_RETRIES_PER_STAGE = 2`,
  `_MAX_ASK_USER_PER_STAGE = 1`.
- `tools/base.py::ToolWeight` thresholds explained in comments only.
- `runtime/artifact_store.py:32`: `INLINE_THRESHOLD = 4096` shadowed
  by `config.artifact_store.inline_threshold_bytes`.

Some of these are in `config.yml`, some are not. The same number
("max retries") appears in both the pipeline runner (per-stage) and
the execution monitor (per-step) with different meanings.

**Best practice.** All tunable knobs go through `config.yml`. Module
constants only when they encode an invariant (e.g. "Anthropic JSON
must have `tool_use_id`"), with a comment explaining why.

**Improvement path.** A 30-minute audit moving these into
`RuntimeConfig` clarifies what is tunable and what is invariant.

### 6.6 Bag-of-globals logger module
`logger.py` mixes formatting, ANSI palette state
(`_councillor_color_map` is *module-level mutable*), session
configuration, and tag helpers. `configure_logging` also initialises
the metrics writer. The colour-by-label assignment isn't process-safe
across council runs (relies on insertion order).

**Improvement path.**
- A `LogFormatting` class owning the palette state.
- Session/metrics configuration moves to a `Session` object owned by
  the Agent; `configure_logging` becomes a pure formatter setup.

### 6.7 Council and pipeline both define "max retries"-style limits
- `Pipeline._MAX_RETRIES_PER_STAGE = 2`
- `Pipeline._MAX_ASK_USER_PER_STAGE = 1`
- `ExecutionMonitorConfig.max_step_retries`
- `ExecutionMonitorConfig.max_defers_per_step`
- `ExecutionMonitorConfig.step_max_tool_calls`
- `_DIRECT_MAX_*` family

These all interact: a step can retry inside `ExecutionStage`, the
stage can RETRY inside the pipeline, the user can be asked to
intervene, and the direct-mode fallback has its own caps. No single
file documents how they combine.

**Best practice.** A single "retry budget" doc-comment somewhere,
ideally on the pipeline runner, listing every cap and which layer
owns it. The numbers themselves are fine; the lack of a map is the
ugly part.

### 6.8 Two redundant intent classifiers
`runtime/classifier.py::IntentClassifier` is marked UNUSED in its
own docstring (line 16-17): "replaced by inline routing header in
agent.py". The `WorkflowSelector` in the same file is still used by
the pipeline. Dead code with a "kept for reference" justification.

**Improvement path.** Delete `IntentClassifier`. Git will remember
it. Keep `WorkflowSelector`, which is genuinely live.

### 6.9 Prompt injection handling has a UI in two stages
Both `ExecutionStage._run_step` and `DirectExecutionStage._run_loop`
embed a synchronous `input(...)` prompt-injection dialog with banner
prints and the option to expel the artifact. This is UI logic in
runtime stages.

**Best practice.** UI escalations go through `UserGate` (which already
exists for guard escalations). The injection gate should produce an
`Escalation`, not directly call `print()` and `input()`.

**Improvement path.** Introduce `Escalation.source = "injection"` and
route through `CLIUserGate.prompt`.

### 6.10 Conversation history in two places, sometimes
`Messenger` holds the canonical conversation. `runtime/artifact_store.py`
also stores conversation history (Tier 2: `save_conversation`,
`load_conversation`). The artifact store dumps the full
`Messenger.get_messages()` at session end. The ORM has a `Step.result`
field that captures step-level results, but no conversation table.
Resume restores from artifact store, not the ORM.

**Improvement path.** Decide where the source of truth lives.
The ORM is the natural choice given §5.7 and the soft-delete /
audit pattern in `db/base.py`.

---

## 7. Cross-cutting recommendations

| Priority | Action                                                                   | Effort | Pays off                                         |
|----------|--------------------------------------------------------------------------|--------|--------------------------------------------------|
| P0       | Add tests for `PlanCriticAdapter.synthesize`, `_pack_chronological`, `ActionGuard`. | S | Locks down the highest-value pure logic.        |
| P0       | Extract shared ReAct loop from `_run_step` and `_run_loop`.              | M      | Halves bug surface in the most-edited code.     |
| P0       | Add `runtime/json_extract.py`, use it everywhere; add Anthropic structured-output path. | S | Removes 6 fragile parsers; cuts retries.        |
| P1       | Stop reading `app_config.config` at import time in stages — inject.      | M      | Unblocks parallel agent runs and per-test config. |
| P1       | Promote `runtime/persistence.py` and `runtime/artifact_store.py` to top-level imports; resolve real cycles. | S | Removes 25 inline imports.                |
| P1       | Split `Plan`/`Step` from `PlanRun`/`StepRun`.                            | M      | Removes in-place mutation; mirrors ORM schema.  |
| P1       | Buffer/batch persistence writes; share one event loop across DAL calls.  | M      | Removes per-step DB latency.                    |
| P2       | Mechanical split of `artifact_store.py` into a package.                  | S      | Reviewability.                                   |
| P2       | Move magic numbers into `RuntimeConfig`; document retry budget.          | S      | Removes hidden coupling.                         |
| P2       | Delete dead `IntentClassifier`.                                          | XS     | Honest dead code.                                |
| P2       | Route prompt-injection escalation through `UserGate`.                    | S      | UI/runtime separation.                           |
| P3       | Long-term: collapse artifact store onto SQLModel.                        | L      | Single persistence story.                        |

---

## 8. What I think is the strongest thing here

If someone asked me what to *protect* during the next refactor, it
would be the pipeline + stage contract and the council deliberation
primitive. Both are domain-agnostic abstractions that are doing real
work, both have correct internal invariants, and both extend cleanly
to the next several roadmap items (Project 5 trace replay, Project 9
VIGIL recovery, Project 11 RL feedback) without rewrites.

If someone asked me what to *attack* first, it would be the duplicated
ReAct loop in the two execution stages — it is the single largest
piece of code that gets touched on every feature change, and every
new provider feature (caching, thinking, streaming) is going to need
to go in twice until it's collapsed.

---

## Open questions for you

1. Is the ORM (`db/`) intended to *replace* the SQLite parts of the
   artifact store, or coexist? My recommendation depends on the
   answer — if replace, then §5.7 and §6.10 become a single
   migration story.
2. Are tests deferred to a specific project number (the README
   mentions Project 5 = observability), or is the absence of
   `_tests/` accidental?
3. How firm is the curriculum framing? Several patterns I'd want to
   lift into shared infrastructure (council, pipeline, tool loop)
   are written as if they belong inside the curriculum's "agent
   runtime" project; are we OK extracting them into a `core/` package
   that later projects depend on?
