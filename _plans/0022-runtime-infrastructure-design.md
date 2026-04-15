# 0022 — Runtime Infrastructure: Design

## Overview

Introduce an execution-time control layer between the agent orchestration
and the model, inspired by Cruz's AI Runtime Infrastructure paper and
Adaptive Focus Memory (AFM). This layer actively observes, validates, and
intervenes during plan execution — replacing the static planning gate with
LLM-based intent classification, validating plans before execution,
monitoring each step for failures, and managing context to prevent bloat
and constraint drift.

```
user message
  → IntentClassifier (LLM) → direct | plan
      ├── direct → _run_loop (existing ReAct)
      └── plan →
            Planner.plan()
              → PlanValidator (code) → valid | retry planner | fall back to direct
              → for each step:
                    ActionGuard.check_step() → ALLOW | BLOCK | ESCALATE
                    ContextManager.pack() → budget-constrained messages
                    _run_step()
                      → for each tool call:
                            ActionGuard.check_tool_call() → ALLOW | BLOCK | ESCALATE
                            tool.execute() (only if ALLOW)
                    ExecutionMonitor.assess() → CONTINUE | RETRY | REPLAN | DEFER | SKIP
              → Synthesizer
```

---

## Motivation

Three failure modes identified in production testing:

1. **Planning gate too narrow** — keyword-based heuristic misses real
   multi-step requests ("analyze X and write a summary to Y") because it
   has no indicator words. The gate fired on 0 of our test cases that
   actually needed planning.

2. **No execution-time feedback** — when a step fails or produces garbage,
   the agent plows through remaining steps anyway. There is no mechanism
   to retry, skip, or re-plan mid-execution.

3. **Context bloat** — large tool outputs (file reads, shell output)
   accumulate in conversation history, causing max_tokens termination at
   step 3 of a 6-step plan. Early constraints (the user's original
   request) get buried under tool output and lose salience.

Cruz's runtime infrastructure paper formalizes the solution: an
execution-time control layer with closed-loop feedback. AFM provides the
specific mechanism for context management — dynamic fidelity assignment
under a token budget.

---

## Architecture

### New module: `src/runtime/`

```
src/runtime/
  __init__.py
  schema.py            # StepDecision, StepAssessment, ValidationResult, FidelityLevel
  classifier.py        # IntentClassifier (replaces PlanningGate)
  validator.py         # PlanValidator (code-only structural checks)
  guard.py             # ActionGuard (pre-execution safety gate for steps + tool calls)
  monitor.py           # ExecutionMonitor (heuristic triage → LLM escalation)
  context_manager.py   # ContextManager (AFM-inspired non-destructive packing)
  compressor.py        # Heuristic compression for tool outputs
  prompts.py           # Prompts for classifier + monitor
```

### New provider: `src/providers/openai.py`

OpenAI provider using the `openai` SDK directly (not through Ollama's
compatibility layer). Enables gpt-4o-mini for lightweight runtime calls.

---

## Component Design

### 1. IntentClassifier

Replaces the keyword-based `PlanningGate` entirely. A small, focused LLM
call that decides whether a user message requires planning.

**Input:** current user message + last N messages of conversation history
(so it understands follow-ups).

**Output:**
```json
{"mode": "direct", "reason": "conversational follow-up to previous work"}
```
or
```json
{"mode": "plan", "reason": "user requests analysis then file write — two distinct operations"}
```

**Model:** configurable separately from the main agent model. Intended for
a small, cheap model (gpt-4o-mini, or a small Ollama model like phi3 or
gemma2:2b). Falls back to the main agent model if not configured.

**Provider:** configurable independently. The classifier can use OpenAI
while the main agent uses Anthropic or Ollama. This requires per-component
provider support (see Provider Changes below).

**Conversation awareness:** the classifier sees the last 6 messages
(configurable) of the real conversation history — not a fresh Messenger.
This prevents the failure mode where the planner was invoked for
conversational follow-ups and hallucinated "I have no previous conversation
history."

**Cost:** ~150 input tokens, ~30 output tokens. Fires on every user
message. At gpt-4o-mini pricing this is effectively free.

**Prompt structure:**
```
System: You classify user messages as requiring a multi-step plan or
direct single-turn execution. Consider conversation context — if the
user is following up on previous work, that is almost always "direct".
Return ONLY a JSON object with "mode" and "reason".

User: [last N messages as context] + [current message]
```

---

### 2. PlanValidator

Code-only structural validation. No LLM call. Runs after the planner
produces a plan, before execution begins.

**Checks:**
- All `action_type` values exist in the registered toolsets
- Step count <= `max_steps`
- No duplicate consecutive steps (same description within edit distance)
- Each step has a non-empty description
- Step numbering is sequential starting at 1

**On failure:** returns specific feedback string. The planner retries once
with the feedback injected. If the retry also fails validation, fall back
to direct execution.

**Why code-only:** if the planner produces logically bad plans, fix the
planner prompt. A second LLM call to review the first is doing the same
work twice. Structural problems (malformed JSON, invalid action types) are
the ones that actually occur and they're trivially caught with code.

---

### 3. ExecutionMonitor

Fires after each step completes. Uses a two-tier assessment:

**Tier 1 — Heuristic triage (code, no LLM call):**
- Result is empty or whitespace-only → FLAG
- Result contains error indicators: "error", "failed", "exception",
  "permission denied", "not found", "I cannot", "I don't have access" → FLAG
- Step action_type was `file_io` and step description mentions "write" but
  no file was created (check via `os.path.exists`) → FLAG
- Tool returned an explicit error → FLAG

If no flags → auto-CONTINUE. No LLM call. This is the fast path that
handles the 80% of steps that succeed cleanly.

**Tier 2 — LLM assessment (only when heuristics flag):**

Small LLM call (same model as classifier — gpt-4o-mini or equivalent).

**Input:**
- Original user query
- Current step description and result
- Summary of completed steps (one line each)
- Remaining steps (descriptions only)
- The specific flag(s) that triggered assessment

**Output:**
```json
{
  "decision": "retry",
  "reason": "tool returned permission denied — retry with sudo or alternative path",
  "suggestion": "try reading the file with a different approach"
}
```

**Decisions:**

| Decision | Behavior |
|----------|----------|
| `CONTINUE` | Proceed to next step as planned |
| `RETRY` | Re-run this step. Inject failure context + monitor's suggestion into system prompt. Max `max_step_retries` attempts (default: 2) |
| `REPLAN` | Call `Planner.replan()` to replace remaining steps. Completed steps are preserved. |
| `DEFER` | Move this step to the end of the queue. Useful when step depends on something not yet available. Max 1 defer per step to prevent infinite loops. |
| `SKIP` | Mark step as skipped, proceed. Used when a step is redundant given prior results. |
| `ESCALATE` | (Future) Surface to user for guidance. For now, treated as CONTINUE with a log warning. |

---

### 4. ContextManager (AFM-inspired)

Non-destructive context packing. The Messenger stores full history as-is.
Before each `provider.chat()` call, the ContextManager produces a
budget-constrained version of the messages.

**Design (adapted from Cruz AFM):**

```
Messenger.get_messages()  (full history)
       │
       ▼
ContextManager.pack(messages, current_query, budget_tokens)
       │
       ├── Score each message:
       │     score = f(semantic_sim, recency, importance)
       │
       ├── Assign fidelity: FULL / COMPRESSED / PLACEHOLDER
       │     score >= threshold_high  → FULL
       │     score >= threshold_mid   → COMPRESSED
       │     score < threshold_mid    → PLACEHOLDER
       │
       ├── Pack chronologically under budget:
       │     Try intended fidelity first
       │     Downgrade if over budget: FULL → COMPRESSED → PLACEHOLDER → drop
       │
       └── Return packed messages
              │
              ▼
       Provider.chat(packed_messages, tools, system)
```

**Scoring (three signals, combined):**

1. **Semantic similarity** — cosine similarity between message embedding
   and current query embedding. Reuses the `all-MiniLM-L6-v2` model
   already loaded for the router. No new dependency.

2. **Recency decay** — exponential half-life decay:
   `w_recency = 0.5 ^ (age_in_turns / half_life)`. Default half_life = 10
   turns.

3. **Importance classification** — rule-based, not LLM:

   | Message type | Importance | Rationale |
   |---|---|---|
   | User's first message (original query) | CRITICAL | Must never degrade — this is the task definition |
   | User follow-up instructions | HIGH | Direct user intent |
   | Assistant reasoning (text blocks) | MEDIUM | Useful for continuity but compressible |
   | Tool results < 500 chars | MEDIUM | Small enough to keep |
   | Tool results >= 500 chars | LOW | Large outputs are the primary bloat source |
   | write_file content in tool_use blocks | LOW | File exists on disk; content is redundant in context |
   | Intermediate tool calls (list_dir, etc.) | LOW | Scaffolding, not substance |

   CRITICAL messages are force-elevated to score 1.0 (always FULL fidelity)
   regardless of recency or similarity. This is the key AFM insight — it's
   what drives the 83% → 0% ablation result.

**Compression (heuristic, no LLM):**

| Content type | Compression strategy |
|---|---|
| Tool result (file read) | First 5 lines + `[... N lines omitted]` + last 3 lines |
| Tool result (shell output) | First 10 lines + `[... N lines omitted]` + last 5 lines |
| write_file tool_use block | `[wrote {N} chars to {path}]` |
| Assistant text | First 2 sentences + last sentence |
| Any content | Hard cap at `compressed_max_chars` (default 300) |

**Placeholder stubs:**
```
[user message, turn 3]
[tool result: read_file /path/to/file — 2847 chars]
[assistant response — discussed analysis approach]
```

**Token estimation:** `len(text) / 4` as initial approximation. Swap in
`tiktoken` later if we add OpenAI provider (tiktoken is an OpenAI
dependency so it comes free). For Anthropic, the approximation is close
enough for budget enforcement — we're not trying to hit the limit exactly,
we're trying to stay well under it.

**Token budget:**

A brief explainer on what token budgets buy:
- 1 token ≈ 4 characters ≈ 0.75 words of English
- A 200-line Python file ≈ 2500 tokens
- A shell command output (100 lines) ≈ 1500 tokens
- The tools schema (all 4 toolsets) ≈ 4000 tokens
- System prompt ≈ 300 tokens
- Model output (max_tokens: 4096) ≈ 4096 tokens reserved

In a 6-step plan, history can reach 30,000+ tokens easily (6 steps ×
tool_use + tool_result + reasoning). Without management, this means every
subsequent API call sends all prior steps' full output, which costs money
and dilutes attention on what matters.

Budget formula:
```
message_budget = model_context_limit - max_tokens - tools_overhead - system_overhead - safety_margin
```

Default: `16384` tokens for message history. This is conservative — keeps
total input well under 30K tokens per call, which is efficient and keeps
attention focused. Configurable in config.yml.

When the conversation is short (< budget), the ContextManager is a no-op —
all messages pass through at FULL fidelity. It only activates when history
exceeds the budget.

---

### 5. Planner.replan()

New method on the existing `Planner` class. Called by the ExecutionMonitor
when it returns `REPLAN`.

**Input:**
- Original user query
- Completed steps with result summaries (one line each)
- The step that triggered replanning + failure reason
- Remaining (now-invalidated) step descriptions as context

**Prompt:**
```
System: You are re-planning the remaining steps of a task. Some steps
have already been completed. One step has failed or produced inadequate
results. Produce a revised plan for the REMAINING work only.

User: [original query]
       [completed steps summary]
       [failed step + reason]
       [original remaining steps — for reference]
       Return a JSON plan with steps numbered starting at {next_step_num}.
       Max {remaining_budget} steps.
```

**Step budget:** `max_steps - completed_count`. The replanner cannot
inflate the plan beyond the original max_steps limit.

**Step numbering:** continues from where the plan left off. If steps 1-3
are done and step 4 triggered replan, new steps start at 4.

**Output:** list of new `Step` objects that replace everything from the
current step onward in the plan.

---

## Provider Changes

### New provider: OpenAI

```python
class OpenAIProvider(BaseProvider):
    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = openai.OpenAI(api_key=api_key)

    def chat(self, messages, tools, system) -> ProviderResponse:
        # Same translation logic as OllamaProvider
        # (Ollama already uses the OpenAI SDK format)
```

The Ollama provider already implements the full OpenAI SDK translation
layer. The OpenAI provider reuses that translation logic but points at
the real OpenAI API instead of a local Ollama endpoint.

**Refactor:** extract the translation methods from `OllamaProvider` into a
shared mixin or base class (`OpenAICompatibleProvider`) that both
`OllamaProvider` and `OpenAIProvider` inherit from.

### Per-component provider selection

The runtime layer needs to use a different model (and potentially different
provider) than the main agent. Settings changes:

```ini
# .env additions
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Runtime model (used by classifier + monitor)
RUNTIME_PROVIDER=openai          # "openai", "anthropic", "ollama", or null (use main)
RUNTIME_MODEL=gpt-4o-mini        # model for runtime calls
```

Factory change:
```python
def get_provider(provider_name=None, model_override=None) -> BaseProvider:
    # If provider_name given, use that; otherwise use settings.llm_provider
    # If model_override given, use that; otherwise use provider's default model
```

The runtime components (IntentClassifier, ExecutionMonitor) call
`get_provider(settings.runtime_provider, settings.runtime_model)` to get
their own lightweight provider instance.

---

## Config Changes

### config.yml additions

```yaml
runtime:
  context_manager:
    enabled: true
    message_budget_tokens: 16384
    half_life_turns: 10
    threshold_high: 0.45
    threshold_mid: 0.25
    compressed_max_chars: 300
  execution_monitor:
    enabled: true
    max_step_retries: 2
    max_defers_per_step: 1
  plan_validator:
    enabled: true
  intent_classifier:
    enabled: true
    context_window: 6          # number of recent messages to include
```

### settings.py additions

```python
# OpenAI
openai_api_key: Optional[str] = Field(default=None, ...)
openai_model: str = Field(default="gpt-4o-mini", ...)

# Runtime layer
runtime_provider: Optional[str] = Field(default=None, ...)  # null = use main provider
runtime_model: Optional[str] = Field(default=None, ...)      # null = use provider default
```

### config.py additions

```python
@dataclass
class ContextManagerConfig:
    enabled: bool
    message_budget_tokens: int
    half_life_turns: int
    threshold_high: float
    threshold_mid: float
    compressed_max_chars: int

@dataclass
class ExecutionMonitorConfig:
    enabled: bool
    max_step_retries: int
    max_defers_per_step: int

@dataclass
class PlanValidatorConfig:
    enabled: bool

@dataclass
class IntentClassifierConfig:
    enabled: bool
    context_window: int

@dataclass
class RuntimeConfig:
    context_manager: ContextManagerConfig
    execution_monitor: ExecutionMonitorConfig
    plan_validator: PlanValidatorConfig
    intent_classifier: IntentClassifierConfig
```

---

## Schema Additions

### `src/runtime/schema.py`

```python
class StepDecision(str, Enum):
    CONTINUE  = "continue"
    RETRY     = "retry"
    REPLAN    = "replan"
    DEFER     = "defer"
    SKIP      = "skip"
    ESCALATE  = "escalate"

@dataclass
class StepAssessment:
    decision: StepDecision
    reason: str
    suggestion: str | None = None  # injected into retry context

class ValidationStatus(str, Enum):
    VALID   = "valid"
    INVALID = "invalid"

@dataclass
class ValidationResult:
    status: ValidationStatus
    feedback: str | None = None    # specific error for planner retry

class FidelityLevel(str, Enum):
    FULL        = "full"
    COMPRESSED  = "compressed"
    PLACEHOLDER = "placeholder"

@dataclass
class ScoredMessage:
    index: int
    message: dict                  # original message from Messenger
    score: float
    importance: str                # "critical", "high", "medium", "low"
    fidelity: FidelityLevel
    token_estimate: int
```

---

## Updated planning/schema.py

The existing `StepFlags` dataclass gains runtime-relevant fields:

```python
@dataclass
class StepFlags:
    retry: bool = False
    escalate: bool = False
    defer: bool = False
    retry_count: int = 0          # NEW — tracks retries for max enforcement
    deferred: bool = False        # NEW — has this step been deferred before?
    skipped: bool = False         # NEW — was this step skipped?
```

---

## Logging

All runtime decisions are logged at INFO level with banners:

```
── Intent classification ──────────────────────────
  mode: plan  reason: user requests analysis then file write
── Plan validation ────────────────────────────────
  status: valid
── Step 2/4 [file_io] ────────────────────────────
  → write_file  /path/to/output.md  (1200 chars)
  ← OK
── Monitor: Step 2/4 ─────────────────────────────
  heuristics: PASS → auto-CONTINUE
── Step 3/4 [shell] ──────────────────────────────
  → run_command  ls /nonexistent
  ← error: No such file or directory
── Monitor: Step 3/4 ─────────────────────────────
  heuristics: FLAGGED (error string in result)
  LLM assessment: RETRY — "directory not found, try parent directory"
── Step 3/4 RETRY (1/2) ─────────────────────────
  ...
```

Additionally, user/assistant messages are now logged (this was an
outstanding request):

```
── User ───────────────────────────────────────────
  analyze /bin/pwd and write a summary to _tests/summary.md
── Assistant ──────────────────────────────────────
  I'll analyze the binary and create a summary for you.
```

---

## Phases

| Phase | Plan doc | What | Files |
|-------|----------|------|-------|
| 1 | 0023 | **OpenAI provider + multi-provider factory** | `providers/openai_provider.py`, refactor `providers/ollama.py` into shared base, update `factory.py`, `settings.py` |
| 2 | 0024 | **Runtime schema + config** | `runtime/__init__.py`, `runtime/schema.py`, `config.yml` runtime section, `config.py` RuntimeConfig |
| 3 | 0025 | **Intent Classifier** | `runtime/classifier.py`, `runtime/prompts.py`, replace `planning/gate.py` usage in `agent.py` |
| 4 | 0026 | **Plan Validator** | `runtime/validator.py`, wire into `agent._execute_plan()` |
| 5 | 0027 | **Execution Monitor + Planner.replan()** | `runtime/monitor.py`, `runtime/prompts.py` additions, `planning/planner.py` replan method, wire into agent step loop |
| 5.5 | 0027b | **Action Guard** | `runtime/guard.py`, wire into `agent._run_step()` and `agent._run_loop()` for tool-call-level gating, and `agent._execute_plan()` for step-level gating |
| 6 | 0028 | **Context Manager** | `runtime/context_manager.py`, `runtime/compressor.py`, wire into agent before all `provider.chat()` calls |
| 7 | 0029 | **Logging + integration** | User/assistant message logging, runtime decision logging, cleanup of `planning/gate.py` |

### Phase dependency graph

```
Phase 1 (OpenAI provider)
   └──→ Phase 2 (schema + config)
           ├──→ Phase 3 (classifier) ─────→ Phase 7 (logging)
           ├──→ Phase 4 (validator)  ─────→ Phase 7
           ├──→ Phase 5 (monitor)    ─────→ Phase 7
           └──→ Phase 6 (context mgr) ───→ Phase 7
```

Phases 3-6 are independent of each other after Phase 2 completes.
Phase 7 is integration/cleanup after all components exist.

---

## What Does Not Change

- `StaticRouter` — used as-is, called with step description
- `ToolRegistry` — unchanged
- `Messenger` — stores full history as-is (ContextManager wraps it non-destructively)
- All tool implementations — unchanged
- `Spinner` — unchanged (receives status updates from new components)
- `Synthesizer` — unchanged
- `Planner.plan()` — unchanged (new `replan()` method added alongside)

---

## What Gets Removed

- `src/planning/gate.py` — replaced entirely by `runtime/classifier.py`
- `PlanningGateConfig` in config.py — replaced by `IntentClassifierConfig`
- `config.yml` `planning.gate` section — replaced by `runtime.intent_classifier`

---

## Deferred

- **ESCALATE implementation** — enum value exists, behavior is CONTINUE
  with a log warning until user-in-the-loop UX is designed
- **LLM-based compression** — heuristic compression first; LLM compression
  (like AFM's LLMCompressor) is a future enhancement if heuristics prove
  insufficient
- **Learnable importance scoring** — rule-based importance first; if it
  misclassifies frequently, consider a small classifier model
- **tiktoken integration** — `chars/4` initially; swap in tiktoken when
  OpenAI provider is added (it's a transitive dependency of the openai SDK)
- **Multi-agent delegation** — runtime layer is designed for single-agent
  execution; multi-agent is a separate feature
- **Plan persistence / audit log** — runtime decisions are logged but not
  persisted to a structured store
