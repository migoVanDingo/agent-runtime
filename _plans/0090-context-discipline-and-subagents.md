# 0090 — Context discipline + sub-agent dispatch

> **Audience:** Implementer with full codebase access, no prior context.
> Read this document end-to-end. Phase docs (`0090a` … `0090e`) will be
> written separately when each phase is scheduled.
>
> **Reading order:** `0079-runtime-as-god.md` (sub-agents must respect this
> tenet) → `0089-pluggable-context-manager.md` (the AFM strategy we're
> extending) → `_papers/recursive-language-models.pdf` §1–§3 (paradigm
> background; we borrow the spirit, not the REPL machinery) → this doc.

---

## 0. North star

Two related goals, motivated by the same underlying problem:

1. **Stop runaway token usage in classifier-style LLM calls** so arc doesn't
   silently exceed per-minute rate limits on the runtime provider as
   sessions deepen. Concrete trigger: session
   `SES01KRRZQY3GPYX8D3WCMD54936K` hit a 429 on `claude-haiku-4-5` after a
   single `RoutingStage` call sent **119,402 input tokens** for what should
   be a 5-10k classification.

2. **Enable scoped sub-agent dispatch** so context-heavy work (Ghidra
   analysis, source reconstruction, multi-file code edits) can be delegated
   to a specialized child agent that owns its own context window. The main
   agent only sees the child's structured result, not its working trace.
   This is the arc-shaped version of the Recursive Language Models paper's
   core insight: *the model shouldn't see everything — it should delegate
   to scoped sub-calls that return small things.*

Both goals reduce the rate at which the main agent's context window grows
and unblock workflows that currently degrade as sessions get long.

---

## 1. Why now — concrete problem data

From session `SES01KRRZQY3GPYX8D3WCMD54936K`:

```
16:34:22  rag_context: context block injected
16:34:22  RoutingStage: started
16:34:25  RoutingStage: in=119,402 out=31  (claude-haiku-4-5)
16:34:26  anthropic._base_client: Retrying request to /v1/messages  ← 429
…8 retries…
```

The user's rate limit is 50,000 input tokens/min. One routing call =
2.4× the limit. The session died.

### Why 119k landed at routing time

AFM packs `messages` to its 65k budget. **But it has no visibility into
the `system` prompt**, which has been growing unboundedly:

- The base agent system prompt
- `build_analysis_manifest()` — lists every paged artifact, no cap
- `context.rag_context` — chunk-RAG block, char-budgeted but not
  token-budgeted, no awareness of total LLM call size
- Tool schemas for every toolset in scope — 40+ tools × ~100 tokens each

So `messages` packs to 65k, `system` swells to 50k+, total = 119k. AFM
did its job correctly; nobody is enforcing a total-budget invariant.

### Why this hits *runtime* LLM calls hardest

`RoutingStage`, `SkillHintStage`, `ExecutionMonitor`, `ImportanceScorer`
all use the runtime provider (haiku in current config) for cheap
classifier-style decisions. Haiku has a 50k/min limit. The main provider
(gemini-2.5-flash in current config) has higher limits and absorbs the
same payload without complaint. **The same context budget is being
applied regardless of which provider receives it**, so the runtime
provider blows up first.

### Aspirational state

For specialized work like *"analyze proc with Ghidra and reconstruct C
source,"* the main agent should not be the entity reading the 12KB
decompile and synthesizing the clone. It should dispatch a Ghidra
sub-agent (with the Ghidra tools, a tight system prompt, possibly a
different provider like Claude Opus tuned for reverse engineering) and
receive a structured `{algorithm, mode, iv, key_derivation, …}` summary
that fits in 500 tokens. The main agent's context stays lean across
turns.

---

## 2. Non-goals

- **No REPL-style RLM scaffold** for arc. The RLM paper's prompt-as-REPL-
  variable design is a poor fit for arc's pipeline architecture and would
  require restructuring the entire agent loop. We borrow the principle
  ("delegate to scoped sub-calls"), not the mechanism.
- **No changes to the pipeline-stage taxonomy.** Routing/Planning/Council/
  Execution/Continuation/Synthesis stay where they are.
- **No changes to existing tool APIs.** Existing `BaseTool` subclasses
  keep returning strings. Sub-agent dispatch is additive.
- **No new RAG query interface** (`query_artifact`-style sub-LLM lookups).
  The marginal value over the existing chunk-injection RAG is small at
  arc's scale and doesn't address the actual cause of the 119k call.
  Reconsider after sub-agents land if telemetry shows artifact reads are
  still a dominant cost.

---

## 3. Design overview

Two prongs, executed in order:

### Prong A — Context discipline (phases 0090a + 0090b)

Make AFM and the system-prompt builders cooperate so total LLM call size
is bounded, with separate budgets for runtime-LLM and main-LLM calls.

- `AFM.pack(messages, query, system_prompt_size)` — pack to
  `total_budget − system_prompt_size`, not to a fixed message budget.
- `ContextConfig.params.afm.runtime_budget_tokens` — a smaller cap
  applied when the current pipeline stage is going to call the runtime
  provider.
- Bound the growth of the system prompt itself: cap the analysis
  manifest, narrow tool schemas per stage, cap the RAG block to a
  fraction of remaining headroom.

### Prong B — Sub-agent dispatch (phases 0090c–0090e)

A `SubAgentRunner` primitive lets a tool or skill spawn a child `Agent`
instance with its own provider, registry subset, system prompt, and
context manager. The child runs its own pipeline (just like the main
agent) and returns a string (or JSON) to the caller. The main agent's
context is unchanged.

- Runtime-as-god preserved: the parent owns the child's lifecycle.
  `pause_check` propagates. Events are linked via `parent_turn_id`.
- Children are *passive* in the runtime-as-god sense — they don't decide
  whether the parent retries/replans/escalates. They just execute their
  scoped task and return.
- Children are *short-lived per skill step* — no long-running sub-agent
  daemons. Spawned, runs the task, returns, dies. Same lifecycle as a
  tool call.

Prong A is necessary even after Prong B — sub-agents themselves still
need to make LLM calls under sane budgets.

---

## 4. Architecture

### 4.1 Files added

```
src/runtime/subagents/
├── __init__.py
├── runner.py          ~200 lines — SubAgentRunner: builds + runs child Agent
├── result.py          ~60 lines  — SubAgentResult dataclass (string + optional structured)
└── registry.py        ~80 lines  — Named sub-agent profiles (provider, toolset, sys prompt)

src/tools/implementations/subagents/
└── ghidra_analyst.py  ~100 lines — first concrete sub-agent (phase 0090d)
```

### 4.2 Files modified

- `src/runtime/context/manager.py` — AFM accepts `system_prompt_size`
- `src/runtime/context/strategies/sliding.py`, `truncation.py` — same
  signature update (or accept `**kwargs` and ignore)
- `src/runtime/context/strategy.py` — Protocol gains `system_prompt_size`
  parameter
- `src/config/runtime.py` — new `runtime_message_budget_tokens` config
- `src/runtime/stages/routing.py`, `skill_hint.py` — pass current
  `system_prompt_size` into `pack(...)`; opt into the runtime budget
- `src/runtime/monitor.py`, `importance.py` — same (their inline messengers
  are small but they still call the runtime provider; runtime budget
  should apply)
- `src/session_paths.py` — `build_analysis_manifest()` gains size cap
- `src/agent.py` — extracts a helper to build a child `Agent` with
  overrides
- `src/runtime/stages/_execution_stage.py` — emit
  `subagent.spawned`/`completed` events when a tool returns a subagent
  result envelope (or wire via a `SubAgentTool` adapter)
- `src/skills/implementations/deep_disassembly.py` — wire the ghidra
  analyst sub-agent as a step (phase 0090d)

### 4.3 The sub-agent protocol

```python
# src/runtime/subagents/runner.py

@dataclass(frozen=True)
class SubAgentSpec:
    """Profile for a sub-agent type — registered ahead of time, used by skills."""
    name: str                          # "ghidra_analyst", "code_writer", etc.
    description: str                   # for telemetry / introspection
    provider: str | None = None        # None = inherit parent's
    model: str | None = None           # None = inherit
    toolset_names: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    system_prompt: str = ""            # specialized system prompt
    response_format: str = "text"      # "text" | "json"
    response_schema: dict | None = None  # required when response_format = "json"
    timeout_seconds: float = 300.0
    max_iterations: int = 20


@dataclass(frozen=True)
class SubAgentResult:
    ok: bool
    text: str
    structured: dict | None = None     # populated when spec.response_format = "json"
    elapsed_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    error: str | None = None


class SubAgentRunner:
    def run(self, spec: SubAgentSpec, task: str,
            *, pause_check=None, parent_turn_id: str | None = None) -> SubAgentResult:
        """Spawn a child Agent, run task, return result. Synchronous, blocks caller.

        The child Agent:
          - has its own Messenger (no parent history)
          - has a narrowed ToolRegistry (only spec.toolset_names)
          - has a narrowed SkillRegistry (only spec.skill_names)
          - uses spec.provider/model if set, otherwise inherits
          - runs the same pipeline as the parent (Routing → Planning → …)
          - is cancellable via the parent's pause_check
          - emits its events with parent_turn_id on the runtime bus
        """
        ...
```

### 4.4 Telemetry surface

New event types (joins 0087 schema):

| Type | When | Payload |
|---|---|---|
| `subagent.spawned` | Before child agent starts | name, provider, model, toolset_names, parent_turn_id |
| `subagent.completed` | After child returns successfully | name, elapsed_ms, tokens_in, tokens_out, cost_usd, response_chars |
| `subagent.failed` | Child errored or timed out | name, error_type, error_message, elapsed_ms |

Events also carry the standard top-level fields from 0087 schema v2
(`session_id`, `turn_id` = parent's, `duration_ms`, `cost_usd`, etc.) so
they show up in the same pandas analyses without extra joins.

### 4.5 Runtime-as-god alignment

| Tenet | How sub-agents preserve it |
|---|---|
| Tools / sub-agents passive | Child agent runs a scoped task, returns data. Never decides whether parent should retry/replan/escalate. |
| Parent owns lifecycle | `SubAgentRunner.run` is synchronous; parent's pause_check is threaded into the child. Cancel propagates. Timeout enforced by parent. |
| Parent has awareness | Child's pipeline events emit on the same bus with `parent_turn_id` linkage. Parent can replay the child's trajectory from telemetry. |
| Parent has control | Child has no state outside its lifetime. Can be killed any time. Doesn't pollute parent's context regardless of outcome. |

---

## 5. Phase breakdown

| Phase | Title | Scope | Risk |
|---|---|---|---|
| **0090a** | AFM + runtime budget split | `runtime/context/manager.py`, strategy protocol, config; thread `system_prompt_size` through pack call sites | Low |
| **0090b** | System prompt bounded growth | Cap analysis manifest, per-stage tool schema narrowing, RAG block scaled to headroom | Low |
| **0090c** | Sub-agent runtime primitive | New `runtime/subagents/`, child Agent builder, lifecycle telemetry | Medium |
| **0090d** | First sub-agent: GhidraAnalyst | `tools/implementations/subagents/ghidra_analyst.py`, wire into deep-disassembly skill | Medium |
| **0090e** | Provider specialization per role | Sub-agent profiles in config, cost/quality telemetry comparisons | Low |

Phases are ordered. 0090a/b are independent of c/d/e and stand alone — even
without sub-agents, they fix the 429. 0090c is the foundational machinery.
0090d proves the pattern on a real workload. 0090e generalizes.

---

## 6. Phase details

### 0090a — AFM + runtime budget split

**Goal:** No single LLM call exceeds its provider's per-minute rate limit
because of unbounded system-prompt growth.

**Changes:**

1. `ContextStrategy.pack` signature gains an optional
   `system_prompt_size: int = 0` parameter. AFM uses it to compute the
   effective budget: `effective = max_total_budget − system_prompt_size`.
   Other strategies (truncation, sliding, rag) accept it for protocol
   compliance and may or may not honor it.

2. `ContextConfig.params.afm` gains a second budget:
   ```yaml
   runtime:
     context:
       params:
         afm:
           message_budget_tokens: 65536        # for main-provider stages
           runtime_message_budget_tokens: 8000  # for runtime-provider stages
   ```

3. `ContextManager.pack` accepts an optional `stage_provider_tier`
   ("main" | "runtime") to select which budget applies. Default: "main"
   (no behavior change for stages that don't pass it).

4. Routing, SkillHint, ImportanceScorer, ExecutionMonitor pass
   `stage_provider_tier="runtime"` and the current system prompt's token
   estimate.

5. New log warning when `system_prompt_size > 0.5 * effective_budget` —
   the manifest/RAG/tool schemas are eating headroom; the next packing
   pass will drop a lot of conversation.

**Verification:**
- Unit test: AFM with `system_prompt_size=40000`, `message_budget=65000`,
  packs messages to ≤25000 tokens.
- Integration test (replays SES01KRRZQY3G…): the routing call after the
  long session sends ≤15k tokens instead of 119k.

**Estimated:** 1 day. ~150 lines changed, ~100 lines test.

### 0090b — System prompt bounded growth

**Goal:** The system prompt has a hard ceiling regardless of how many
artifacts, tools, or RAG hits exist.

**Changes:**

1. `session_paths.build_analysis_manifest()` already caps at 20 entries
   but doesn't cap total character size. Add `max_chars` (default 4000),
   truncate with `"... (X more)"` line.

2. Per-stage toolset narrowing. Currently many stages get the full
   tool-schema list. Add a `stage_toolset_hint` field to
   `PipelineContext` that the relevant stage can populate, and have
   `RoutingStage` / `SkillHintStage` / runtime-LLM-only stages skip tool
   schemas entirely (they don't call tools). Main-provider stages narrow
   to the toolsets relevant to the current plan step's `action_type`.

3. RAG injection budget made aware of total LLM call size.
   `config.rag.injection_budget_chars` is a fixed char cap today; change
   it to take whichever is smaller: the fixed cap OR
   `(effective_total_budget − messages_packed_size − system_prompt_base_size)
   × 0.3`. Effect: when context is tight, RAG injects less.

4. Optionally cap the RAG block at `max_chunks` (default 6) — currently
   12.

**Verification:**
- Unit test: 100-artifact session → `build_analysis_manifest()` ≤ 4000
  chars.
- Unit test: RoutingStage's system prompt ≤ 8000 tokens regardless of
  toolset count.
- Integration: replay a deep-session JSONL through the routing path and
  confirm system prompt stays bounded as artifacts accumulate.

**Estimated:** 1 day. ~120 lines.

### 0090c — Sub-agent runtime primitive

**Goal:** A `SubAgentRunner` that can be invoked from a tool or skill to
run a scoped child Agent.

**Changes:**

1. New `src/runtime/subagents/spec.py` with `SubAgentSpec` and
   `SubAgentResult` dataclasses (see §4.3).

2. New `src/runtime/subagents/registry.py` — process-level registry of
   `SubAgentSpec` instances. Specs can be registered programmatically
   (built-ins) or via the plugin system (entry-point group
   `arc.subagents`, future). Lookup by name.

3. New `src/runtime/subagents/runner.py:SubAgentRunner`:
   - `run(spec, task, *, pause_check=None, parent_turn_id=None) → SubAgentResult`
   - Builds a child `Agent` via `agent.build_scoped_agent(spec, parent)`:
     - Narrowed `ToolRegistry` (only `spec.toolset_names`)
     - Narrowed `SkillRegistry` (only `spec.skill_names`)
     - Child `Messenger` (empty)
     - Child `ContextManager` (its own state)
     - Provider override if `spec.provider` set, otherwise inherits
     - Custom system prompt if `spec.system_prompt` set
   - Calls `child_agent.call(task, checkpoint_fn=pause_check)`
   - Captures `child_agent.last_response`, token deltas, cost
   - Emits `subagent.spawned`/`completed`/`failed` events on the same
     bus, tagged with `parent_turn_id` (and a child `turn_id` so the
     child's own pipeline events can join back)
   - Respects `spec.timeout_seconds` — if the child doesn't return in
     time, raise `SubAgentTimeoutError`, emit `subagent.failed`,
     parent's calling tool/skill decides what to do (typically returns
     an error string the agent can react to)

4. New `src/agent.py:build_scoped_agent(spec, parent)`:
   - Extracts the existing `Agent.__init__` logic that does provider /
     registry / context_mgr / skill_registry construction.
   - Accepts overrides from `spec`.
   - Reuses parent's `user_gate` so escalations go through the same UI
     (sub-agents shouldn't prompt the user behind the user's back; if
     a child wants to escalate, it goes through the parent's gate).

5. New runtime events in `runtime/events/schema.py` taxonomy:
   `subagent.spawned`, `subagent.completed`, `subagent.failed`.

6. A `SubAgentTool` adapter in `src/tools/base.py` (or new
   `tools/subagent_tool.py`) — wraps a `SubAgentSpec` as a `BaseTool`
   subclass so any spec can be exposed to the agent as a regular tool.
   The tool's `execute()` dispatches to the runner. This is how skills
   invoke sub-agents — they "just" call a tool.

**Critical design choice — same process, not subprocess.**
Sub-agents run in the same process on the same worker thread that
invokes them. They block the caller synchronously. This is intentional:
- No serialization cost between parent and child
- `pause_check` propagation is trivial (just pass the callable)
- The cost is that a runaway child can hang the worker thread — but the
  child has its own iteration cap and per-call LLM timeout, just like
  the parent, so this is bounded.
- Subprocess isolation is reserved for cases where native runtimes hang
  (Ghidra — already covered by 0090's predecessor work on subprocess
  pyghidra). Sub-agents are pure Python + LLM calls, no native runtime
  hazard.

#### Escalation propagation

Sub-agents reuse the **parent's** `user_gate` and `input_gate`. When a
tool inside the sub-agent's pipeline hits `ActionGuard.ESCALATE` (e.g.,
`ghidra_analyze` on a host binary) or a stage emits `ASK_USER`, the
prompt flows through the same TUI escalation channel the user already
knows. The runtime-as-god tenet stays intact — parent owns the user
interaction surface; child can never bypass it.

Surfacing requirements:

- The escalation prompt is **prefixed with the sub-agent scope**:
  `[subagent:ghidra_analyst] host execution: ghidra_analyze on 'proc'`.
  Without this prefix the user has no way to tell whether a prompt
  comes from the main plan or a delegated sub-task.
- Hard failures inside the child (timeout, max iterations, unrecovered
  tool error) propagate as `SubAgentResult(ok=False, error=...)`. The
  **parent's** calling tool/skill decides whether to surface them as a
  user-visible message or replan around them — same as how it handles
  any other tool failure today. The child never decides on its own to
  push a failure modal to the user.
- **Single-active-escalation invariant**: because v1 sub-agents are
  synchronous, only one agent (parent or child) is mid-execution at
  any moment, so two escalations never compete for the gate. This
  invariant is what makes shared-gate safe; if 0093 introduces async
  sub-agents the gate will need a queue.

#### Recursion prevention (no sub-sub-agents in v1)

Hard-prohibit sub-agents from spawning their own sub-agents. Two-layer
enforcement so an LLM, a future plugin, or a regression can't slip
through:

1. **Registry filter at child construction.** When `SubAgentRunner`
   builds the child's `ToolRegistry`, it filters out every
   `SubAgentTool` instance regardless of which toolset the parent
   exposed. The child's LLM sees zero `subagent:*` tools — it can't
   even propose one in its plan.

2. **Contextvar tripwire at runner entry.** A module-level
   `contextvars.ContextVar[bool] _inside_subagent` is set to True for
   the duration of `SubAgentRunner.run`. If the runner is entered
   while the flag is already True, it raises immediately with
   `SubAgentRecursionError: sub-agents may not spawn further
   sub-agents (recursion not permitted in v1)`. This catches any
   programmatic path that bypasses the registry filter (e.g., a
   plugin that calls `SubAgentRunner.run` directly).

Both are required: the filter prevents the LLM from being told the
capability exists; the tripwire is the actual safety guarantee.
Lifting either to enable recursion is a deliberate future-plan
decision (queued as 0094) with budget propagation and depth caps.

#### Logging discipline — three visually distinct tiers

The `session.log` must let the user scan and immediately see the
boundary between main-agent work, runtime-LLM work, and sub-agent
work. Three additions:

1. **`contextvars.ContextVar[str | None] _log_scope`** drives a
   logging filter that prefixes every record with a scope tag:

   | Scope | Tag | Set by |
   |---|---|---|
   | Main agent (default) | `[main]` | (default) |
   | Runtime LLM call | `[runtime]` | `RoutingStage`, `SkillHintStage`, `ImportanceScorer`, `ExecutionMonitor` for the duration of their LLM call |
   | Sub-agent execution | `[subagent:ghidra_analyst]` | `SubAgentRunner.run` for the duration of the child's pipeline |

2. **Scope-aware banner**: `runtime.utils.banner` reads the contextvar
   and produces nested-indented + dimmed-color banners when inside a
   sub-agent scope. So when reading the log you see the sub-agent's
   stage transitions as a contiguous indented block, visually distinct
   from the parent's stages.

3. **`agent_scope` event field** on every runtime bus event. Values:
   `"main"`, `"runtime"`, `"subagent:<name>"`. This lets pandas
   analyses group / split by scope directly without traversing
   `parent_turn_id` linkage. Joins 0087 schema v2 cleanly — just one
   new top-level string field.

The same contextvar drives the runtime-vs-main budget selection in
0090a (single source of truth for "which provider tier am I in"). So
0090a/b/c all read the same context and one place to look when
debugging "why is this LLM call sized that way."

TUI bonus (not strictly required, easy to do): the spinner reads the
contextvar and displays the active scope:
- main: `⚙ ghidra_analyze ...   ·   2:33`
- sub-agent: `⚙ [ghidra_analyst] ghidra_analyze ...   ·   2:33`

**Verification:**
- Unit test: register a trivial echo spec, run via `SubAgentRunner.run`,
  confirm response + cost telemetry + events fire.
- Unit test: spec with `response_format="json"` and a schema, child
  returns conformant JSON, runner parses and populates
  `SubAgentResult.structured`.
- Unit test: timeout fires, `subagent.failed` event emitted, parent
  receives error.
- Integration: parent calls sub-agent in a turn, confirm parent's own
  message history is unchanged after sub-agent returns.

**Estimated:** 3 days. ~500 lines code + 200 lines tests.

### 0090d — First sub-agent: GhidraAnalyst

**Goal:** Prove the pattern on a real workload. Replace the
deep-disassembly skill's "read big artifact, synthesize source" step
with a sub-agent dispatch.

**Changes:**

1. New `src/tools/implementations/subagents/ghidra_analyst.py` —
   registers a `SubAgentSpec`:
   ```python
   GhidraAnalystSpec = SubAgentSpec(
       name="ghidra_analyst",
       description="Specialized reverse-engineering sub-agent",
       toolset_names=("reversing", "file_io"),
       skill_names=(),
       provider=None,   # inherit, OR pin to specific provider in config
       model=None,
       system_prompt=GHIDRA_ANALYST_PROMPT,
       response_format="json",
       response_schema={
           "type": "object",
           "properties": {
               "algorithm": {"type": "string"},
               "mode": {"type": "string"},
               "iv": {"type": ["string", "null"]},
               "key_derivation": {"type": "string"},
               "round_function": {"type": "string"},
               "constants": {"type": "array"},
               "summary": {"type": "string"},
           },
           "required": ["algorithm", "summary"],
       },
       timeout_seconds=600.0,
       max_iterations=15,
   )
   ```
   And exposes it as a `SubAgentTool(GhidraAnalystSpec)` registered
   into the `subagent` toolset.

2. Modify `src/skills/implementations/deep_disassembly.py` so the
   synthesis step calls `subagent:ghidra_analyst` (analogous to how
   skills currently call `skill:<name>`) instead of `read_file
   _analysis/<binary>/ghidra_decompile.txt`. The structured response is
   piped into the next step (the source-reconstruction synthesis).

3. The analyst sub-agent's system prompt is the reverse-engineering
   methodology hints currently embedded in `deep_disassembly.py`'s
   `_CRYPTO_HINT` block, expanded with the new lessons captured during
   the past week of debugging (two's complement DELTA representation,
   CBC IV byte spotting, TEA vs XTEA round structure, dynamic ECB-vs-CBC
   testing). These move from the skill's per-step prompt into the
   analyst's persistent system prompt.

**Verification:**
- End-to-end: replay the `proc` analysis prompt through the TUI. The
  analyst sub-agent runs, reads ghidra artifacts within its own
  context, returns structured JSON. The parent agent receives a ~500-
  token summary instead of dumping 12k of decompile into its context.
- Compare main-agent context size at end of turn vs the pre-0090
  baseline. Expected: 30-50% reduction.
- Token-cost telemetry per turn: total cost should be comparable
  (sub-agent does the heavy lifting), but distribution shifts — main
  agent's tokens drop, sub-agent's tokens appear in separate events.

**Estimated:** 2 days. ~300 lines.

### 0090e — Provider specialization per role

**Goal:** Different sub-agent profiles use different LLM providers/models
without code changes — declared in config.

**Changes:**

1. `SubAgentSpec.provider` and `SubAgentSpec.model` already exist (0090c).
   This phase wires them up via config so users can override at runtime:

   ```yaml
   subagents:
     ghidra_analyst:
       provider: anthropic
       model: claude-opus-4-7
     code_writer:
       provider: openai
       model: gpt-5-codex
     verifier:
       provider: anthropic
       model: claude-haiku-4-5
   ```

2. New `SubAgentConfig` dataclass parsed from `config.yml`. Merged into
   the registered spec at lookup time, so the spec's defaults can be
   overridden without editing code.

3. Per-sub-agent cost telemetry on `subagent.completed` events:
   `provider`, `model`, `input_tokens`, `output_tokens`, `cost_usd`.
   Joins 0087's existing cost-tracking pipeline.

4. Optional `arc subagent list` CLI command (parallels `arc plugin list`
   from 0088) showing registered specs, their providers, and any config
   overrides.

5. Documentation: README section "Sub-agents" with config examples and
   how to introduce new specs.

**Verification:**
- Configure ghidra_analyst → opus, code_writer → gpt-5-codex, run a
  session that uses both. Verify `subagent.completed` events show the
  right providers. Verify cost telemetry separates main agent from
  sub-agent costs.
- `arc subagent list` shows configured specs.

**Estimated:** 1.5 days. ~200 lines.

---

## 7. Backwards compatibility

- AFM strategy signature change (`system_prompt_size` parameter) is
  additive with a default of 0 → existing callers work unchanged.
- `runtime_message_budget_tokens` is new optional config; missing →
  falls back to `message_budget_tokens`.
- Existing tools, skills, and pipeline stages don't change. Sub-agents
  are an additive capability invoked through the existing tool/skill
  surface.
- The legacy `read_file` path on ghidra artifacts continues to work; the
  deep-disassembly skill changes which path it prefers, but other
  skills/agents calling `read_file` on `.arc/analysis/...` are
  unaffected.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Sub-agent runaway (infinite loop, excessive cost) | Timeout per sub-agent (default 5 min), `max_iterations` cap, cost surfaced in telemetry so it's visible. Parent's `pause_check` propagates. |
| Sub-agent escalation deadlock (child wants user input while parent is paused) | Child inherits parent's `user_gate`; if the gate is currently mid-prompt the child's prompt queues behind. UI sees only one prompt at a time. |
| AFM signature change breaks alternative strategies (truncation, sliding, rag) | Phase 0090a updates the Protocol and all three strategies to accept the new parameter; tests cover all four strategies. |
| Tool-schema narrowing accidentally drops a tool a stage needs | Routing/SkillHint don't call tools at all (they classify), so they can safely receive zero tool schemas. Main-LLM stages get the toolsets their plan step's action_type references. Conservative default: if unsure, include the toolset. |
| Per-stage toolset hint doesn't reach all the places it needs to | Add an integration test that walks a full pipeline and asserts each LLM call's `tools` array is correctly narrowed. |
| Sub-agents fragment cost-tracking visibility | Telemetry events carry `parent_turn_id` so dashboards can roll up parent + child costs per turn. |
| Sub-agent JSON output isn't conformant to the spec schema | Use providers' structured-output / tool-call trick (already implemented in `providers/anthropic.py`) to force schema-conformant output. On failure, retry once with the schema embedded in the system prompt; on second failure, return the raw text and surface a warning. |
| Hidden coupling: sub-agent assumes parent's session_id (e.g., reads from same RAG) | Sub-agent uses parent's `session_id` for RAG access (so it can leverage cached analysis artifacts) but its own `turn_id` for telemetry. Documented invariant: sub-agents share session-scoped data, not turn-scoped data. |

---

## 9. Open questions

**Q1.** Should sub-agents have access to the parent's conversation history
at all? Recommend **no** by default — that defeats the isolation goal —
but allow `SubAgentSpec.inherit_history: bool = False` for niche cases.

**Q2.** How does the parent agent address a sub-agent? Three options:
(a) as a tool (`subagent:ghidra_analyst` step type), (b) as a skill
(`skill:ghidra_analyst` step type), (c) as its own action type in the
plan schema. Recommend **(a)** — keeps the surface familiar, reuses
guard/escalation infra, no plan-schema changes.

**Q3.** Per-stage toolset narrowing (0090b) — should this be a
`PipelineContext` field set by each stage, or inferred from the plan's
action types? Recommend hybrid: inferred from `action_type` by default,
overridable via context field for stages that know better.

**Q4.** Should runtime-LLM budget apply at the strategy level (AFM
chooses a smaller budget when called from a runtime stage) or at the
stage level (stage decides what budget to ask for)? Recommend
**strategy level**, driven by the same `_log_scope` contextvar used
by the logging discipline mechanism (§6 0090c). Single source of
truth: the scope contextvar tells both "what to log" and "which
budget to use." Stages set scope on entry; AFM reads it on each
`pack` call.

**Q5.** Does the analyst sub-agent share the parent's `.arc/ghidra/projects/`
cache? Recommend **yes** — sub-agents are session-scoped, the project
cache is session-shared, no isolation concern. First call from parent or
child populates the cache, subsequent calls from either reuse it.

**Q6.** What happens when a sub-agent's response doesn't fit the JSON
schema after retries? Recommend: return the raw text in
`SubAgentResult.text` with `structured=None` and a warning in
telemetry. Calling skill decides whether to abort or continue with the
partial result.

**Q7.** Plugin system integration for sub-agents (entry-point group
`arc.subagents`) — in scope for 0090e or punt to a follow-up? Recommend
**punt to 0091** to keep 0090 tight; built-in specs are enough for the
first release.

---

## 10. Success criteria (end of 0090e)

1. Replaying session `SES01KRRZQY3GPYX8D3WCMD54936K`'s routing call
   sends ≤15k input tokens instead of 119k. No 429.
2. Running the `proc` analysis prompt produces a working `proc_clone.c`
   (encryption output matches `./proc`) using the GhidraAnalyst
   sub-agent, with main agent tokens-per-turn down ≥30% vs current.
3. `arc subagent list` shows registered specs with configured
   provider/model.
4. Telemetry `subagent.spawned`/`completed`/`failed` events present in
   `runtime.jsonl` with parent linkage.
5. Pandas analysis cleanly separates main-agent cost from sub-agent
   cost per turn via `parent_turn_id`.

---

## 11. Out of scope for 0090 — queued for after this feature ships

These are queued and intended to be addressed once 0090 lands. They are
NOT idle "future work" — each has a concrete trigger or motivation tied
to 0090's success criteria. Reconsider scope at 0090e completion.

- **0091**: Plugin entry-point group `arc.subagents` so third-party
  plugins can ship specs. Currently sub-agent specs are built-in or
  declared in `config.yml`. Plugins can already ship tools/skills via
  0088's mechanism; extending to specs is the natural next step.
- **0092**: `query_artifact` tool — RLM-style chunk-query against
  artifacts. Trigger: after 0090d, measure how often the GhidraAnalyst
  sub-agent still has to dump full artifacts into its own context. If
  significant, add `query_artifact` so the analyst can issue targeted
  queries against its working artifacts the same way the parent
  delegates to it.
- **0093**: Async sub-agent dispatch — parent fires off multiple
  sub-agents in parallel, awaits all. Requires the gate to grow a
  queue (the single-active-escalation invariant breaks under
  concurrency). Only worth doing if profiling shows serial sub-agent
  calls are a bottleneck.
- **0094**: Sub-agent recursion (sub-agent spawning sub-sub-agents).
  v1 hard-prohibits this (see §6 0090c "Recursion prevention"). Lift
  only with explicit depth limit, budget propagation, and recursion
  cost telemetry.

---

## 12. Reading order for the implementer

1. `_plans/0079-runtime-as-god.md` — sub-agents must respect this.
2. `_plans/0089-pluggable-context-manager.md` — the strategy machinery
   0090a/b extends.
3. `_papers/recursive-language-models.pdf` §1–§3 — paradigm background;
   we're applying the spirit (delegate scoped sub-calls) at the
   sub-agent layer rather than the in-context REPL layer.
4. This document.
5. The relevant phase doc.
