# 0086 — Runtime-as-god drift audit

> **Audience:** Implementer with full codebase access, no prior context.
> Read `0079-runtime-as-god.md` first (establishes the foundational tenet).
> Then read this document end-to-end. Each finding is self-contained with
> a file path, line number, drift severity, recommended fix, and risk note.

---

## 0. Foundational rule recap

Per `_plans/0079-runtime-as-god.md`, the **Runtime Infrastructure**
(`src/runtime/`) is the sole owner of these decisions:

1. When execution pauses (escalation)
2. When the future is rewritten (replanning)
3. When a step is retried / reconsidered
4. When a plan is accepted / rejected
5. Which tools are available at any moment
6. Whether the task is complete
7. Whether the goal is achieved

Three derived rules:

1. **Plan metadata is descriptive, never prescriptive.**
2. **Skills are passive building blocks.**
3. **Stages own all control flow.**

Drift = any code outside `src/runtime/` that makes one of those decisions, or
any code inside `src/runtime/` that gets that decision from non-runtime
metadata.

This audit covers `src/tools/`, `src/skills/`, `src/planning/`, `src/providers/`,
and `src/service/`, and also flags drift that remains *inside* `src/runtime/`
where a decision is sourced from the wrong place.

The findings are grouped by severity (critical → moderate → minor → noted-only).
After the findings, §6 lists areas that were audited and found **clean**, to
give confidence in coverage.

---

## 1. Critical findings

### CRIT-1 — `planning/planner.py:110` retries on the agent-decision level

**Location**: `src/planning/planner.py:110–131` (the `Planner.plan` method).

```python
if plan is None and config.planning.retry_on_invalid:
    logger.info("Planner: invalid response — retrying once")
    messenger.add_assistant_message(response.content)
    messenger.add_user_message(
        "Your response was not valid JSON or did not match the required schema. "
        "Try again. Return ONLY the raw JSON object, nothing else."
    )
    response = self._safe_chat(...)
    ...
```

A similar retry block exists in `Planner.revise` (`planner.py:186–207`).

**Why this is drift**: this is *not* HTTP/network retry (which would be
infrastructure). It is "the model produced output we couldn't parse — retry
the agent-level operation." Whether and when to re-attempt a failed
sub-operation is a runtime decision per the tenet. Today this is buried inside
the component that runs the operation, and the runtime has no visibility into
it.

That said: this is a *parse-failure recovery* loop, which is arguably "noise
suppression close to the noise source" — and consistent with how the
providers handle HTTP 429s (CRIT-N below: NOT drift).

**Severity: CRITICAL.** Two reasons:

1. **It is not a 1-attempt-then-fail noise filter — it actually re-invokes
   the LLM with new content (a clarification message).** That's a small
   conversation policy decision, exactly the kind 0079 says belongs to the
   runtime.
2. **Schema-failure retry is exactly the kind of brittleness telemetry needs
   to measure.** Hiding it inside `Planner` makes it invisible to monitoring
   and prevents the runtime from making smarter policy decisions
   (back-off, alternate-prompt, escalate-to-user-when-N-fails).

**Fix**:

- Move the retry decision to `PlanningStage`. Have `Planner.plan` return
  either a parsed `Plan` or a `PlanParseFailure` (with the raw model output
  and what the parser couldn't accept). The stage decides whether to retry.
- Same for `Planner.revise` (called from `CouncilStage` on a critic rejection).
- Add a single `PlanningPolicy` config block: `max_parse_retries: int = 1`,
  `parse_retry_strategy: str = "schema_hint"`.
- Stage code (rough):
  ```python
  outcome = self._planner.plan(user_message, ...)
  attempts = 0
  while isinstance(outcome, PlanParseFailure) and attempts < cfg.max_parse_retries:
      outcome = self._planner.plan(
          user_message, ...,
          schema_correction_hint=outcome.error,
      )
      attempts += 1
  if isinstance(outcome, PlanParseFailure):
      # Pipeline retry mechanism takes over (StageStatus.RETRY)
      return StageResult(status=StageStatus.RETRY, reason=outcome.error)
  ```

**Risk**: medium. The retry is currently invisible — moving it out makes the
behavior the same but adds an explicit policy knob. Backwards-compat: the
default `max_parse_retries: 1` exactly reproduces today's behavior. No user-
visible change.

---

### CRIT-2 — `planning/schema.py:82` `retry_count` is on the planner-schema

**Location**: `src/planning/schema.py:76–99` (`StepRuntimeState`).

```python
@dataclass
class StepRuntimeState:
    """Runtime-managed state for a step. Never set by the planner or skills."""
    retry_count: int = 0
    deferred: bool = False
    skipped: bool = False
```

The dataclass *says* it is never set by the planner. It is *not*. But:

1. The dataclass lives inside `src/planning/schema.py`, where the planner DTOs
   live. Any new contributor adding a "step persistence" field is one
   refactor away from putting a non-runtime field beside `retry_count`.
2. `Step.from_dict` reads `flags` from the plan-JSON dict (`planning/schema.py:149–151`),
   which means if a planner *ever* produced a plan with `flags.retry_count: 3`,
   that value would be accepted and the runtime would see it as state.
3. The 0079f phase doc was supposed to collapse `Step.flags` to a runtime
   state container; this lands but it lives in the wrong module.

**Severity: MODERATE.** No active drift, but the placement is structurally
fragile. The runtime owns the lifecycle of this state — therefore the type
should live in `src/runtime/`.

**Fix**:

- Move `StepRuntimeState`/`StepFlags` to `src/runtime/run_state.py` (which
  already imports `Step`).
- `Step` keeps `flags: StepRuntimeState | None = field(default=None)` and
  the runtime sets it post-parse:
  ```python
  step.flags = step.flags or StepRuntimeState()
  ```
- Update the JSON serialization: `Step.to_dict` should not include `flags`
  (plans coming out of the planner are descriptive only). `Step.from_dict`
  should not read `flags` from JSON either; if a stale plan is loaded with
  one, ignore it and create a fresh `StepRuntimeState()`.
- The DB persistence in `runtime/persistence.py:312` already reads
  `step.flags.retry_count` — that path stays.

**Risk**: low. The change is internal to how `Step.flags` is sourced. A unit
test that calls `Plan.from_dict({...})` on a plan with `flags` baked in needs
updating.

---

### CRIT-3 — `skills/implementations/test_reconstruction.py:80–133`: `continuation_steps` mutates plan semantics

**Location**: `src/skills/implementations/test_reconstruction.py:80–133`.

```python
def continuation_steps(
    self, ctx: SkillContext, prior_results: list[Step],
) -> list[Step] | None:
    """Return steps for one fix-and-retest iteration.
    The infrastructure (not the skill) decides how many times this is called
    — bounded by max_iterations and gated by StructuralCriteria above.
    """
    oracle, candidate = self._extract_paths(ctx.original_query)
    n = ctx.starting_step_number
    return [Step(...), Step(...), Step(...), Step(...)]
```

The docstring claims the skill is passive — and it mostly is. But this method
is called from `ContinuationStage._build_continuation_plan` (line 267) which
unconditionally takes the returned steps and constructs a new `Plan`. The
runtime is not making a decision: it's accepting whatever the skill returns.

**Why this is drift** (subtle): when `continuation_steps` is called, the skill
encodes the *strategy* for the next iteration ("read the report, read the
candidate, fix it, rerun diff_behavior"). That's reasonable for a skill —
this is its "what to do" knowledge. **What pushes it into drift**: the skill
embeds a 4-step plan when 2 might be enough. The runtime cannot inspect or
revise the iteration plan; it just executes it.

**Severity: MODERATE.** The pattern is acceptable for skill expansion (the
skill knows the domain) but conflicts with the spirit of "runtime owns
control flow." A truly faithful design would have the skill return a
`continuation_intent` (e.g., "fix and rerun") and the planner generate the
steps to fulfill that intent.

**Fix**: deferred — this is a design tension, not a bug. The 0079g phase doc
established this pattern. Recommend keeping it for now, but document the
boundary clearly:

- Skills may return concrete steps for continuation.
- The runtime is permitted to drop, reorder, or supplement those steps based
  on `ContinuationState` or `CompletionCriteria`.
- The runtime is **not permitted** to read iteration counts or capability
  flags from the skill output.

Audit follow-up: a future plan (`0090-skill-continuation-intent.md`?) could
replace `continuation_steps` with `continuation_intent` and let
`Planner.replan` generate steps. Out of scope for this audit; flag only.

**Risk**: none right now — informational.

---

## 2. Moderate findings

### MOD-1 — `runtime/stages/execution.py:230` reads `step.flags.retry_count` from the dataclass

**Location**: `src/runtime/stages/execution.py:230, 242–254, 322, 353, 358, 360, 403`.

`ExecutionStage._execute_plan` reads and writes `step.flags.retry_count`
directly. That's fine — this *is* the runtime — but the field lives in
`src/planning/schema.py` (see CRIT-2). After CRIT-2 is fixed (state moves to
`src/runtime/run_state.py`), these reads remain in the runtime and are clean.

**Severity: NONE on its own.** Listed for completeness; resolves when CRIT-2
lands.

---

### MOD-2 — `runtime/stages/synthesizer.py:26` reads `step.flags.retry_count`

**Location**: `src/runtime/stages/synthesizer.py:26`.

```python
if s.flags.retry_count > 0 or s.flags.skipped:
```

Synthesizer is *reading* runtime state to decide how to phrase the summary
(e.g., "step 3 was retried twice"). This is acceptable — synthesizer is in
the runtime, and reading state is allowed.

**Severity: NONE.** Audited and clean.

---

### MOD-3 — `skills/implementations/dynamic_analysis.py:84–104` step descriptions encode "Phase 1/2/3/4"

**Location**: `src/skills/implementations/dynamic_analysis.py:81–177`.

```python
# Phase 1: Structure — identify functions and find cipher/key function address
struct_steps = [Step(...), Step(...), Step(...)]
# Phase 2: Dynamic traces — two inputs for differential analysis
trace_steps = [Step(...), Step(...)]
# Phase 3: Step trace — walk the inner loop
step_steps = [Step(...)]
# Phase 4: Synthesis
synthesis_steps = [Step(...)]
```

The skill structures its expansion into phases but each step is concrete and
the iteration counts (e.g., "trace twice for differential") are embedded in
step descriptions, not in skill metadata.

**Why this is drift (mild)**: 0079 explicitly forbids iteration counts in
step descriptions. The "differential trace" pattern (two `lldb_trace` calls
with different inputs) is hard-coded as two steps. If the runtime decided
"this binary needs three traces because two were inconclusive," the skill
can't accommodate that.

**Severity: MINOR.** The skill is producing a fixed-shape expansion. This is
acceptable for a skill, but the phrasing "trace #1" and "trace #2" in the
step descriptions (lines 113, 121, 124) is the prescriptive part — those
strings will be wrong if the runtime drops or duplicates a step.

**Fix**: rephrase step descriptions to not embed positional metadata
("trace the binary…" rather than "this is trace #1"). Add a `tag: str` field
to the skill output if the cross-step reference is genuinely needed (e.g.,
"trace_a" and "trace_b") and have ContinuationStage carry tags forward when
inserting continuation steps.

**Risk**: very low. Pure text change to skill prompts.

---

### MOD-4 — `runtime/stages/skill_hint.py` (whole file, 46 lines)

**Location**: `src/runtime/stages/skill_hint.py`.

```python
class SkillHintStage(Stage):
    """Set context.skill_hint via the WorkflowSelector classifier.
    Advisory only — never produces a plan.
    """
```

This is **clean** — it sets a hint, the planner reads the hint, the planner
decides whether to use it. The runtime owns the decision (via the planner
LLM). Audited and clean. Listed because it's adjacent to the historical
`_WORKFLOW_PATHS` drift that 0079b fixed.

**Severity: NONE.** Audited and clean.

---

### MOD-5 — `service/inprocess.py` cancellation logic (lines 359–399)

**Location**: `src/service/inprocess.py:359–399`.

`checkpoint()` is called from the worker thread at pipeline yield points
(`runtime.pipeline_context._pause_check`). It checks `_cancel_event` and
blocks on `_pause_event`. When the user clicks cancel from the UI, the
service raises `TurnCancelledError` from the worker thread.

**Why this is potentially drift**: the question "should this turn keep
running?" is a runtime-class decision per the tenet. Today, the runtime asks
the service (via `_pause_check`) "should I yield?" and the service replies
"raise TurnCancelledError." That's the runtime asking the service for input,
not the service making the decision unilaterally — *if* the service is just
relaying user intent.

**Severity: NONE on inspection.** The service is the user's surface: when the
user clicks cancel, the user has made the decision to abort. The service is
the courier. This is consistent with how `TUIUserGate` works for escalations
(the user decides; the runtime asks).

**Audited and clean.**

---

## 3. Minor findings

### MIN-1 — `providers/anthropic.py:16, 44, 79` `_RETRY_DELAYS = (1, 2, 4)`

**Location**: `src/providers/anthropic.py:16` and `src/providers/openai_compat.py:10`.

```python
_RETRY_DELAYS = (1, 2, 4)

for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
    try:
        response = self.client.messages.create(...)
        break
    except anthropic.RateLimitError as exc:
        last_exc = exc
        if delay is None:
            raise
        time.sleep(delay)
```

These retry on HTTP `RateLimitError` (429). **This is HTTP/network retry,
not agent retry.** It is infrastructure (the request layer compensating for
transient network noise) and is exactly what the tenet permits.

**Severity: NONE.** Audited and clean.

(Caveat: only `RateLimitError` is caught. A 503 / connection error would
propagate without retry. That's a robustness gap, not a drift issue.)

---

### MIN-2 — `tools/implementations/container/tools.py:262` "no OCI runtime available" check

**Location**: `src/tools/implementations/container/tools.py:262–263`.

```python
session = ContainerSession()
if not session.available():
    return json.dumps({"error": "no OCI runtime available..."})
```

A tool checks for its own backing capability and returns a structured error.
The runtime sees the error and the monitor decides what to do (retry, defer,
replan, escalate). The tool is not making a runtime decision — it's reporting
a fact.

**Severity: NONE.** Audited and clean. This is the *correct* pattern.

---

### MIN-3 — `tools/implementations/reversing/ghidra_*.py:48` `if not ghidra_home(): ...`

**Location**: each Ghidra tool (`ghidra_analyze.py:28`, `ghidra_callgraph.py:48`,
`ghidra_decompile.py:70`, `ghidra_functions.py:49`, `ghidra_find_constants.py:70`).

```python
if not ghidra_home():
    return json.dumps({"error": "Ghidra is not installed..."})
```

Same pattern as MIN-2. Tool reports capability absence; runtime decides.

**Severity: NONE.** Audited and clean.

---

### MIN-4 — `tools/implementations/web/http_request.py` docstring mentions "ESCALATE"

**Location**: `src/tools/implementations/web/http_request.py:4`.

```python
"""http_request — structured HTTP client tool.

...
All requests are ESCALATE in the guard — the agent must get user approval
before any outbound HTTP call.
...
"""
```

The docstring *describes* a guard policy. The tool itself doesn't enforce it.
`runtime/guard.py` classifies the tool. The tool is passive.

**Severity: NONE.** Audited and clean.

(The docstring is also factually correct per the guard's rule for outbound
HTTP — verify with `ActionGuard.check_tool_call` if regressions appear.)

---

### MIN-5 — `tools/implementations/container/adapters.py:49` `runs_locally: bool = False`

**Location**: `src/tools/implementations/container/adapters.py:49, 102, 109, 126, 143`
and `tools/implementations/container/tools.py:245, 350`.

```python
class Adapter:
    runs_locally: bool = False

class NativeBinaryAdapter(Adapter):
    runs_locally = True

# In RunTargetTool / DiffBehaviorTool:
if adapter.runs_locally:
    results = [adapter.run_locally(spec, c) for c in cases]
else:
    # container path
```

This is the historical DRIFT-10 from 0078: an adapter declaring an isolation
policy (host vs. container) on its class. Per the tenet, **policy is runtime
territory** — a tool/adapter should declare its needs ("this is a binary that
exists on host filesystem") and the runtime should map needs → execution
backend.

**Severity: MODERATE** (deferred to a future plan, per 0079 §0.10).

**Fix** (not in this audit's scope — for a future plan):

- Rename `runs_locally` to `runs_in_isolation: bool` and invert.
- Or better: introduce `AdapterCapabilities` with fields like
  `requires_compiler`, `runs_existing_binary`, `mutates_host_fs`. The runtime
  maps capabilities → backend via config (`runtime.execution_policy`).

**Risk**: medium. Affects `RunTargetTool`, `DiffBehaviorTool`, `FuzzTargetTool`,
and all four adapter subclasses. Document in this audit as known-deferred.

---

### MIN-6 — `tools/implementations/search/web_search.py:84` returns a "wait before retrying" hint

**Location**: `src/tools/implementations/search/web_search.py:84`.

```python
return "Error: Brave API rate limit hit — wait before retrying"
```

Tool reports an error including a *retry hint* in the message. The tool does
not decide to retry; the runtime monitor reads the message and decides. This
is descriptive, not prescriptive.

**Severity: NONE.** Audited and clean. (The phrasing "wait before retrying"
is a hint to the LLM, not a directive to the runtime.)

---

## 4. Other audited paths — no drift found

These were inspected in this audit and found clean. Listed so the next
auditor can skip them or verify they remain clean as the code evolves.

### 4.1 All `src/tools/implementations/**` (~80 tools)

- Tools return strings (success or error). They never call back into the
  runtime, never read `RuntimeIdentity`, never set retry/escalate flags.
- Capability checks (`ghidra_home()`, `ContainerSession.available()`) return
  structured errors. Runtime decides.
- Paging logic (`runtime/tool_executor.py:_maybe_page`) lives in the runtime,
  not the tool. **Clean.**
- `runtime.token_tracker.get_tracker().record(...)` is called from inside
  providers — this is telemetry, not control flow. Clean.

### 4.2 All `src/skills/implementations/**` (10 skills)

- Read each skill's `expand()`. None call `platform.system()`, none branch on
  `ContainerSession.available()`, none look up `config.runtime.*`.
- One soft drift (MOD-3, `dynamic_analysis.py` step labels encoding ordinal
  position). All other skill files clean.
- `completion_criteria` is declarative (`StructuralCriteria` /
  `LLMJudgedCriteria`). Skills never *evaluate* their criteria — the
  `ContinuationStage` does. Clean.

### 4.3 `src/planning/synthesizer.py`

- Pure text-generation component. Reads plan, produces summary string. No
  control flow. **Clean.**

### 4.4 `src/planning/prompts.py`

- String templates and a `build_tool_list` / `build_skill_list` helper.
- No code paths, no decisions. **Clean.**

### 4.5 `src/providers/**` (anthropic, openai_compat, factory, base, capabilities)

- `providers/base.py` chat wrapper emits `llm.call.started/completed/error`
  events around `_chat_impl`. Pure instrumentation. **Clean.**
- HTTP-level retries (MIN-1). Network drift OK.
- `providers/capabilities.py` is a dataclass of provider abilities (`tool_use`,
  `streaming`, etc.). Pure data. **Clean.**
- `providers/factory.py` returns a provider instance based on config. No
  control-flow decisions during runtime. **Clean.**

### 4.6 `src/service/` (events.py, interface.py, builder.py, queue.py, translator.py)

- `service/translator.py`: pure function `translate(RuntimeEvent) -> AgentEvent | None`.
  Maps event names. No decisions. **Clean.**
- `service/queue.py`: bounded queue with drop policy for `TokenChunk`. The
  drop decision is infrastructure (backpressure), not runtime control flow.
  Lifecycle events are protected. **Clean.**
- `service/events.py`: dataclasses only. **Clean.**
- `service/interface.py`: Protocol definitions. **Clean.**
- `service/builder.py`: constructs Agent + InProcessAgentService. Reads
  config, no policy decisions. **Clean.**
- `service/inprocess.py`: see MOD-5 — the cancellation path was inspected and
  judged clean.

### 4.7 `src/runtime/**` (the universe)

This is *supposed* to own all control flow. Audit looked for the inverse:
runtime decisions being made based on data that came from outside the runtime
in a load-bearing way.

- `runtime/pipeline.py:128` retries a stage based on `StageStatus.RETRY` — a
  runtime concept set by stages (which are runtime). **Clean.**
- `runtime/stages/execution.py:142–144`: when `step.tool` is unset and
  `action_type` ≠ `CONVERSATION`, the stage falls back to router-selected
  toolsets. The data source is the planner's `step.tool` field
  (descriptive — "use this tool" hint) and the action_type. The decision
  ("expose router-selected tools because the planner didn't pick one") is
  the runtime's. **Clean.**
- `runtime/stages/execution.py:283–296`: reads `step.produces` and emits an
  informational log if the artifact isn't registered. Advisory only —
  doesn't change control flow. **Clean.**
- `runtime/monitor.py`: produces `StepDecision` (CONTINUE / RETRY / REPLAN /
  DEFER / SKIP / GOAL_ACHIEVED / ESCALATE). This is exactly where the
  decision lives. **Clean.**
- `runtime/guard.py`: produces `GuardDecision` (ALLOW / BLOCK / ESCALATE).
  Same. **Clean.**
- `runtime/stages/continuation.py`: the new ContinuationStage from 0079e.
  Reads skill `completion_criteria`, evaluates them or runs an LLM judge,
  decides LOOP / SYNTHESIZE / DONE. **Clean.**

### 4.8 `src/ui/` — out of scope but spot-checked

- `ui/app.py` etc. consume `service.events()` and call `service.send/pause/
  cancel`. The decisions ("user clicked pause") originate with the user;
  the UI is the courier. **Clean.**

### 4.9 `src/routing/` — out of scope but spot-checked

- `routing/static_router.py` and `routing/conditions.py` compute toolset
  matches from message text. Routing is *suggesting* tools; the runtime
  decides whether to use them. **Clean.**

---

## 5. Summary by severity

| Severity | Count | Items |
|---|---:|---|
| Critical | 1 | CRIT-1 (planner-level retry) |
| Moderate | 5 | CRIT-2 (StepRuntimeState placement), CRIT-3 (skill continuation steps — informational), MOD-3 (dynamic_analysis labels), MIN-5 (`runs_locally` — deferred per 0079) |
| Minor    | 5 | MIN-1, MIN-2, MIN-3, MIN-4, MIN-6 — all audited and **clean** |
| Clean (no drift) | 7 paths | §4.1–§4.9 |

**Net actionable work in this audit**: CRIT-1, CRIT-2, MOD-3.

MIN-5 is deferred per 0079 §0 to a future plan covering adapter capabilities.
CRIT-3 is informational (a design tension to be revisited if it becomes a bug).

---

## 6. Recommended remediation order

### Phase 0086a — CRIT-2 first (the structural one)

Move `StepRuntimeState`/`StepFlags` from `planning/schema.py` to
`runtime/run_state.py`. Update `Step.from_dict` to ignore `flags` from JSON.
Update `Step.to_dict` to omit `flags` (runtime state is not part of the plan
JSON contract). Add a unit test that confirms a plan JSON without `flags`
round-trips through the planner correctly.

**Risk**: low. **Time**: 1–2 hours.

### Phase 0086b — CRIT-1 (the retry-policy hoist)

Move retry-on-invalid logic out of `Planner.plan` and `Planner.revise` into
`PlanningStage` and `CouncilStage` (the latter calls revise on critic
rejection). Add `PlanningPolicy` config with `max_parse_retries: int = 1`.
Have `Planner.plan` return `Plan | PlanParseFailure`.

**Risk**: medium. **Time**: 4–6 hours including tests.

### Phase 0086c — MOD-3 (label hygiene)

Edit `skills/implementations/dynamic_analysis.py` to remove positional
phrasing from step descriptions ("trace #1", "trace #2", "the previous step").
Reword to be runtime-invariant.

**Risk**: very low. **Time**: <1 hour.

---

## 7. Open questions

**Q1**: should `PlanParseFailure` (CRIT-1 fix) be a typed dataclass or an
exception? Recommend dataclass — exceptions are control-flow, dataclasses
are data, and per 0079 the runtime should decide based on data.

**Q2**: should CRIT-3 (skill `continuation_steps`) be re-examined after
ContinuationStage has run in production for a quarter? Recommend yes — file
as `0090-skill-continuation-intent.md` if real-world failure modes emerge.

**Q3**: where should the `PlanningPolicy` config live? Recommend a new
field on `PlanningConfig` in `src/config.py` (alongside `retry_on_invalid`,
which can then be deleted once CRIT-1 lands — the config field's intent
moves to the stage).

---

## 8. Verification

After each remediation phase:

1. `pytest -x -q`
2. Run the canonical fix-loop end-to-end (the test-reconstruction skill
   loop). It must still converge.
3. Run a deliberately malformed prompt that the planner can't parse on the
   first try. Check that the stage-level retry fires once and the runtime
   sees the failure properly.
4. `grep -rn "step.flags\." src/planning/` — should return 0 results after
   CRIT-2 lands.
5. `grep -rn "retry_on_invalid" src/planning/` — should return 0 results
   after CRIT-1 lands (the field moves out of `PlanningConfig`).

---

## 9. Reading order for the implementer

1. `_plans/0078-opus-refactor-brief.md` (drift catalog history)
2. `_plans/0079-runtime-as-god.md` (the foundational tenet — most important)
3. This document
4. Phase doc for the specific phase being implemented (0086a/b/c — if these
   spawn — otherwise execute directly against §6 above)
