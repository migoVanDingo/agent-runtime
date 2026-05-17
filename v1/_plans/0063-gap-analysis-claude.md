# 0063 — Gap analysis: codex's refactor vs 0053 design

> Reviewing what landed in `src/` and `tests/` against the eight phases
> in 0053 (claude design) and the eight phase docs codex produced
> (0055–0062). This is *not* a rewrite of 0053 — it's a punch list of
> what still needs doing and a few places where the implementation
> deviates from intent in ways worth flagging.
>
> Read 0053 §5 alongside this. Phase numbers below refer to my plan.

---

## 1. Headline

Codex implemented the **easier 50% of every phase** and **completely
deferred** Plan/PlanRun, container/DI, ORM consolidation, and `_projects`
cleanup. The structural events, sandbox, path policy, and shared
tool-call helper landed and are working end-to-end (I can see clean
JSONL events from a real run). But the load-bearing refactors — the
ones that *enable* the rest — were skipped.

Net assessment: **good plumbing, no architecture change**. The agent
in `src/agent.py` is structurally identical to where we started.

---

## 2. Score per 0053 phase

| Phase | 0053 goal | Status | Where it lives |
|-------|-----------|--------|----------------|
| 0 — Cleanup + tests | Delete `_projects/`, README, pytest scaffold, ≥30 unit tests | **PARTIAL** | `_projects/` still present; README still describes the obsolete curriculum; 12 unit tests in `tests/test_runtime_phase0.py` (unittest, not pytest) |
| 1 — Container, kill singletons | Container in `main.py`; no module-level config reads in stages | **NOT DONE** | 29 files still `from app_config import config` at module level; `agent.py` unchanged; no `Container` class anywhere |
| 2 — JSON contracts + Anthropic structured output | Single tolerant parser used everywhere; Anthropic structured output via single-tool trick | **PARTIAL** | `runtime/json_extract.py` exists and is good. Adopted by **only critic**. Monitor, classifier, importance, planner-fallback, routing all still hand-parse. `ProviderCapabilities` declared `structured_json_schema=False` for Anthropic and the actual structured-output path is not implemented. |
| 3 — Tool loop extraction | Both stages <100 lines, shared `ToolLoop` with hooks | **NOT DONE** | `ToolCallExecutor` (the per-tool-call piece) extracted, ~130 lines. `ExecutionStage._run_step` is still **503 total file lines** and `DirectExecutionStage._run_loop` is **250**. The duplicated *loop* still exists in two places — only the inner per-call body was deduplicated. |
| 4 — Bash sandboxing | Docker preferred, mac/host fallback, escalation flow, mount allow/deny, resource limits | **MOSTLY DONE** | `runtime/sandbox/{base,docker,host,manager}.py` is in. `bash_exec` delegates. Default backend is `docker`, host fallback configurable. **Missing**: `MacSandboxExec`, `network=outbound|restricted` modes (only `none` and "not disabled"), no escalation flow for "approve host execution this once", no sandbox events emitted, no per-session container reuse (every call spawns a fresh `docker run`). |
| 5 — Structured events | Typed events, JSONL emitter, redactor, loader, exporter | **PARTIAL** | `runtime/events/{schema,bus,runtime}.py` is real and working. `_events/{session}.jsonl` produced. **Missing**: redactor (privacy class is recorded but no scrubbing happens), loader (the export script flattens to CSV but there's no pandas API), DuckDB/parquet export, schema doc (`SCHEMA.md`). Event coverage is thin — only 6 event types emitted (session/turn ×3 + tool.call ×3 + policy.decision). No stage events, no LLM-call events, no council events, no escalation events, no plan/step events, no sandbox events, no error events. |
| 6 — Plan vs PlanRun | Spec/run separation; no in-place mutation | **NOT DONE** | `planning/schema.py` unchanged. `Step.flags.retry_count`, `Step.status`, `Step.result`, `Step.error` still mutated in place during execution. ORM models already have the right shape; Python types still don't. |
| 7 — Persistence onto ORM | One artifact store backed by SQLModel; `_store/artifacts.db` migrated into `data/agent.db` | **NOT DONE** | `runtime/artifact_store.py` is the same 1278 lines of raw SQLite. Both DBs still exist and are independent (`_store/artifacts.db` and `data/agent.db`). |
| 8 — Cleanup pass | Magic numbers in config, retry-budget map, dead `IntentClassifier` deleted, direct-mode tool authorization, logger split | **NOT DONE** | `_DIRECT_MAX_*` constants still in stages; `_MAX_RETRIES_PER_STAGE` still in pipeline; `IntentClassifier` still present; logger module unchanged; direct mode still uses unrestricted toolset on ABORT fallback. |

Bonus from codex (not in 0053):

- **Path policy** (`runtime/policy/paths.py`) — applied to all 10
  file_io tools. **0053 didn't call this out**; it's a real value-add
  and a cleanly separated module. Solid work.
- **`RuntimeIdentity` correlation IDs** — prefixed ULIDs for session/
  turn/pipeline/plan/plan-run/step/tool-call. The shape is correct
  and ready for §3.6 below.
- **`ProviderCapabilities`** dataclass — small but useful
  scaffolding for the eventual capability-aware behaviour 0053 §5.2
  asked for.
- **`ToolResult` structured type** — separates `ok`, `content`,
  `error_code`, `metadata`. Currently only round-trips through
  `to_llm_content()` so the LLM still sees the same string, but the
  shape is right for 0053's structured-event story.

---

## 3. Where codex missed the mark

These are not "didn't do" issues — they're "did, but the
implementation has a problem worth flagging."

### 3.1 The "shared tool executor" misses the actual duplication

Codex's 0056 says the goal is "start collapsing duplicated planned/
direct tool-call execution behavior" and extracts `ToolCallExecutor`.
That helper covers *guard, escalate, execute, restore-spinner* — about
60 lines per stage of duplication.

The real duplication is the **outer loop** — the iteration cap, the
tool-call cap, the `force_end` machinery, the repeat-tool-call detector,
the consecutive-error correction, the dangling-`tool_use` patching at
`max_tokens`, the prompt-injection quarantine flow, and the message-
appending. That's ~350 lines per stage and it's still there.

Codex's own doc admits this ("the main ReAct loops still live in their
existing stage files"), but that's the substance of Phase 3 — without
collapsing the outer loop, you've removed maybe 15% of the parallel
code, not the bulk.

**What's needed:** the `core/tool_loop.py` (or `runtime/tool_loop.py`)
with an explicit hook interface. The `ToolCallExecutor` should be a
*member* of that loop, not an alternative to it.

### 3.2 Sandbox per-call cost

`SandboxManager.run_shell` does `docker run --rm ...` *every call*.
With docker startup latency around 200–500 ms on macOS, every shell
command pays that cost. The 0053 design said "container start is
amortised: one container per session". Codex's implementation is the
ephemeral-per-call variant.

This isn't wrong functionally, but it makes the sandbox slow enough
that users will be tempted to set `backend: host`. We should
re-introduce per-session container reuse before this becomes a "turn
the sandbox off" pressure.

**What's needed:** a `LongLivedDockerSandbox` that creates one
container at session-start, executes via `docker exec`, and tears
down on session-end. The current per-call `DockerShellBackend` stays
as a fallback for environments where long-lived containers are
problematic.

### 3.3 Sandbox "fallback" silently degrades

`SandboxManager._is_docker_infrastructure_failure` detects "cannot
connect to docker daemon" and falls back to host *automatically*
when `allow_host_backend=true` is set (which is the default). The
warning is logged + prepended to stdout, but if a multi-user or
CI environment has docker briefly unavailable, the agent runs the
command on the host without consent.

0053 §6.1 said: "Sandbox failures … degrade to a loud-warning host
fallback, not a crash, when `backend: auto` is set; explicit
`backend: docker` fails fast." Codex implements the auto behaviour
under the *explicit* `backend: docker` config, which is the wrong
direction.

**What's needed:** when `backend: docker` is set, infrastructure
failure is fatal. Auto-fallback only when `backend: auto` (which
isn't even a valid value yet — there's no `auto` mode).

### 3.4 Network policy is binary

Sandbox config has `default_network: "disabled"` and the docker
backend translates that to `--network none`. There's no `outbound`
or `restricted` mode, and there's no per-call "approve network
this time" escalation. 0053 §6.1 specified three modes; we have one.

This becomes a real gap as soon as a workflow needs to `pip install`
or `curl` an API legitimately — today the sandbox can't allow it
without flipping the global default, which then leaves every
command with network access.

### 3.5 Path policy doesn't escalate

`PathPolicyDecision.allowed=False` returns an `Error: …` string
directly to the model. There's no `UserGate.prompt()` integration
even though 0058's "Remaining Work" lists this. The model sees the
failure and either gives up or tries a different path. The user
never gets asked "this tool wants to read `/etc/hosts` — approve?".

This is consistent with the guard's design (which *does* escalate),
so the inconsistency is itself a smell. Either both file boundaries
escalate, or both deny outright.

### 3.6 Identity is propagated through a global

`runtime/events/runtime.py` keeps `_identity: RuntimeIdentity` as a
**module-level global** that gets mutated by `set_runtime_identity()`
in `main.py` before each turn. Tool calls then read it via
`get_runtime_identity()`.

For events emitted in linear control flow this works. As soon as
the council deliberates in a thread pool (which it does —
`ThreadPoolExecutor` in `runtime/council.py:222`), the global races
across threads and councillors will emit events with the wrong
identity. This is also why every event has `pipeline_run_id: null`
and `plan_id: null` — the linkage was never wired through
`PipelineContext`. 0062 says this explicitly: "still process-local
identity propagation … not yet a fully explicit dependency passed
through every stage."

**What's needed:** `RuntimeIdentity` lives on `PipelineContext` (and
on each `Stage.run` call frame), not in a module global. The global
should be a session-only fallback. Stages mint their own scoped IDs
(`for_pipeline`, `for_plan`, `for_step_run`) and write them back to
the context.

### 3.7 Anthropic capabilities lie

`AnthropicProvider.capabilities = ProviderCapabilities(structured_json_schema=False)` —
correct for the codepath, but the planner *passes* `json_schema` to
Anthropic and Anthropic *silently ignores it* (`providers/anthropic.py:23-30`).
That's the same status quo as before the refactor; we just have a
flag now that explains why it's broken.

The capability flag is useful only if callers consult it. They
don't. 0061's "Remaining Work" lists this ("Make planner/router/
critic select behavior based on `provider.capabilities`") — until
that lands, the flag is documentation, not enforcement.

### 3.8 Privacy classification is a label, not a behaviour

Every event carries `privacy: {classification: "internal", redacted: true, raw_content_stored: false}` —
but **the redactor doesn't exist**. `redacted: true` is a constant
default. The exporter doesn't redact. The emitter doesn't redact.
So when other users start running this and exporting datasets,
their tool inputs / response previews / message previews will
include real user content with no scrubbing.

This is a footgun for the multi-user analytics goal you mentioned.
The label needs to actually mean something before any export
happens.

### 3.9 Tests are unittest, not pytest

12 tests in `tests/test_runtime_phase0.py` using `unittest`. They
work and they're worth having. 0053 specified pytest because:

- pytest has parametrize, which the council-synthesis test needs (8+ branches)
- pytest has fixtures, which the integration tests will need
- pytest has plugins (coverage, xdist) that are stronger than the unittest ecosystem
- the project's `pyproject.toml` already has Python 3.11+; pytest is the modern default

Not load-bearing — this is reversible at any time. But since we're
about to add many more tests, settling the runner now is cheaper
than migrating later.

### 3.10 `_projects/` still exists

You said delete it. Codex didn't. The entire `_projects/` tree is
still there with 12 sub-directories. README still references it.
This was item #1 in your message after my 0053 — straightforward
miss.

---

## 4. What's still missing entirely

These are 0053 items that have **zero footprint** in the current
code:

1. **Container / dependency injection** — `Container` class, no
   constructor injection of `config` into stages. Every stage still
   pulls `from app_config import config` at module top.
2. **Plan vs PlanRun split** — `runtime/run_state.py` does not exist.
3. **Persistence consolidation** — artifact store still a 1278-line
   raw-SQLite blob.
4. **`_projects/` removal**.
5. **README rewrite**.
6. **`IntentClassifier` deletion** — 200 lines of dead code with a
   "UNUSED" docstring still importable.
7. **Magic-number consolidation** — `_DIRECT_MAX_*`,
   `_MAX_RETRIES_PER_STAGE` still hardcoded.
8. **Retry-budget map / docs** — no single document explaining how
   pipeline retries, stage retries, monitor retries, ASK_USER caps,
   and direct-mode caps interact.
9. **Logger module split** — `_councillor_color_map` still a
   process-mutable global; `configure_logging` still couples
   formatting to metrics-writer init.
10. **Direct-mode tool authorization** — DirectExecutionStage on
    ABORT fallback still gets the unrestricted toolset.
11. **ToolLoop class** — only the inner per-call helper exists.
12. **Anthropic structured output** — capabilities flag is False;
    actual single-tool implementation absent.
13. **JSON parser adoption** in monitor, classifier, importance,
    planner-fallback, routing.
14. **Stage-level event emission** — no `stage.started`/`stage.finished`.
15. **Provider event emission** — no token usage / latency events.
16. **Council event emission** — `council.round`, `council.synthesis`,
    `council.decision` all absent.
17. **Plan-level event emission** — `plan.created`, `plan.revised`,
    `step.started`, `step.completed`, `step.failed`, `replan.triggered`
    all absent.
18. **Sandbox event emission** — `sandbox.run` absent.
19. **Escalation event emission** — guard / monitor / injection
    escalations don't show up in the event stream.
20. **Redactor module** — `observability/redactor.py` doesn't exist.
21. **Loader module** — pandas-API loader doesn't exist.
22. **Parquet export** — only CSV with a flat schema.
23. **Event schema doc** — `observability/SCHEMA.md` doesn't exist.
24. **Per-session sandbox container** — every call is fresh `docker run`.
25. **Sandbox network policies** — only `none` vs not.
26. **Sandbox host-execution escalation flow** — auto-fallback exists
    but no "approve this single host call" path.
27. **Identity on `PipelineContext`** — still process-local global.
28. **Plan/run/step IDs in events** — every event has them as null.

---

## 5. Things codex got right

To keep the report honest:

- **Sandbox abstraction shape** is correct: `Backend` protocol +
  `Manager` selection + `Request`/`Result` value types. Easy to
  add `MacSandboxExec` and `LongLivedDockerSandbox` against this
  interface.
- **Path policy** is a clean module separation that 0053 didn't
  even propose. The shape (workspace + read roots + write roots,
  separate from sandbox config) is the right one.
- **Event schema** is well-versioned (`schema_version: "1.0"`) and
  `RuntimeIdentity.to_event_fields()` is the right way to inject
  correlation IDs without leaking the dataclass internals.
- **`extract_json` implementation** is materially better than the
  old `_extract_json` — the balanced-brace walker handles array
  payloads and nested strings correctly. Once it's adopted
  everywhere, six fragile parsers collapse cleanly.
- **The phase docs themselves are honest** — every doc has a
  "Remaining Work" section that names what was deferred. Codex
  didn't pretend Phase 4 was complete; the docs flag what's
  missing. That's the right culture for incremental refactors.
- **Tests run cleanly**: `python3 -m unittest discover -s tests`
  passes 12 cases in 9ms.
- **End-to-end events work**: I can `tail _events/SES*.jsonl` from
  a real session and see structured records flowing.

---

## 6. Recommended next steps, in priority order

The order is "what's most leveraged for the *rest* of the refactor",
not "what's easiest". Easy wins are tagged "S".

### P0 — leverage / unblockers

1. **Identity on `PipelineContext`** (M). Until plan/step/pipeline
   IDs flow through context, every later event-stream improvement
   has to revisit the same wiring. Without this, dataset analysis
   can't answer "which step did this tool call belong to?".
2. **Container/DI** (M). Every other phase wants to hand a stage a
   new dependency (sandbox, emitter, ORM session). The longer we
   defer this, the more constructors we have to revisit twice.
3. **Plan vs PlanRun split** (S–M). Cheap given how cleanly the ORM
   already separates spec from run. Doing this *before* persistence
   consolidation makes Phase 7 mechanical.
4. **Stage-level events + escalation events + council events** (M).
   This is the single highest-leverage thing for your "logs as
   dataset" goal. Today the JSONL is too thin to answer any
   interesting question.

### P1 — close partial work

5. **Adopt `extract_json` in monitor, classifier, importance,
   planner-fallback** (S). Mechanical, makes the scaffolding actually
   useful.
6. **Anthropic structured output via single-tool trick** (S). Then
   `ProviderCapabilities.structured_json_schema=True` for Anthropic.
   Then planner/critic/etc. branch on capabilities.
7. **Outer ReAct loop extraction** (M–L). The real Phase 3. Use the
   existing `ToolCallExecutor` as a member; both stages become thin
   wrappers.
8. **Redactor + privacy enforcement on export** (S). The
   privacy-class label needs to mean something before any export
   leaves a machine.
9. **Per-session docker container** (S). Order-of-magnitude latency
   improvement; prevents users from disabling the sandbox.
10. **Sandbox network policy: `outbound | restricted`** (S–M) and
    **escalation flow for one-shot host execution** (S).

### P2 — cleanup

11. **Delete `_projects/`** (XS).
12. **Update README** (XS).
13. **Delete `IntentClassifier`** (XS).
14. **Magic numbers → config + retry-budget doc** (S).
15. **Path policy escalates** instead of denying outright (S).
16. **Logger module split** (S).
17. **Switch tests to pytest** (S).

### P3 — bigger refactors, do once the above is in place

18. **Persistence consolidation onto ORM** (L). Easier after
    Plan/PlanRun split and after Container exists.
19. **Loader (pandas) + parquet export** (M). Right after redaction
    is real.
20. **Per-stage adoption of provider capabilities** (M).

---

## 7. One concrete thing I'd ship next, today

If you give me one phase to do next, it would be **a combined
"identity-on-context + stage/escalation/council/plan event
emission"** push. Concretely:

- Add `identity: RuntimeIdentity` to `PipelineContext`.
- `Pipeline.run` mints `pipeline_run_id`; each stage's `run()`
  receives the identity through context.
- `PlanningStage` → `for_plan(plan_id)`; `ExecutionStage` →
  `for_step_run(step_run_id)` per step.
- Emit `stage.started`/`stage.finished` from the pipeline runner
  (one place, not per stage — the runner already has every stage's
  `name`).
- Emit `llm.call` from `provider.chat` (one place, every provider).
- Emit `council.round`/`council.synthesis` from `Council.deliberate`.
- Emit `escalation.requested`/`escalation.resolved` from `UserGate`.
- Emit `plan.created`/`plan.revised`/`step.started`/`step.completed`/
  `step.failed` from the execution stage.

That's ~6–8 emission sites total and it transforms the dataset from
"we logged a few tool calls" into "we have a complete causal trace
of every session". Without this, items P1+ produce more events but
they still won't join cleanly.

The other candidate for "next thing" is **outer ReAct loop
extraction** — but that's structurally bigger and more disruptive,
and the value of doing it now is dampened until P0 lands. Identity
+ events is the higher-leverage move.

---

## 8. Summary in one paragraph

Codex shipped the small modular pieces (sandbox backends, path
policy, event types, JSON helper, identity dataclass, capabilities
flag, structured tool result) and one functional event sidecar.
Nothing it shipped was wrong. But it skipped the four refactors
that would actually change the architecture (Container/DI, Plan/
PlanRun split, persistence consolidation, outer ReAct loop
extraction) and it deferred the privacy/redaction/loader work that
makes the event stream useful as a dataset. The current state is
"the agent runs, plus a thin event sidecar with null correlation
IDs". The next push needs to be **identity through context + thick
event coverage** so the dataset story isn't fictional, followed by
the real Phase 3 (outer loop) and Phase 1 (Container). Phases 6
and 7 are still the right shape and can wait until those land.
