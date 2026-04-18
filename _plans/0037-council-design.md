# 0037 — Council: Multi-Agent Consensus Primitive

**Date**: 2026-04-18
**Status**: Design
**Motivation**: Single-critic adversarial review exhibits systematic bias — the critic drops `strings`
for binary analysis tasks because it applies KNOWLEDGE CHECK and REDUNDANCY rules without domain
context. Rather than hardcoding carve-outs, we replace the single critic with a committee of N
independent (or debating) agents whose synthesis requires consensus to sustain a strong challenge.
Heterogeneous providers supply cognitive independence; debate mode surfaces argumentation even on
same-provider committees.

Based on: Council Mode paper (Wu et al., 2026), multi-agent debate (Du et al., 2023).

---

## Problem Statement

Current state:
- `PlanCritic` runs one primary critic and optionally one consensus critic on high-risk plans
- `_merge_results` keeps the *stronger* suggestion when critics disagree → overconfident drops
- Challenge text truncated at 80 chars in logs (fixed separately)
- No per-model telemetry, no metrics persistence, no user-sided-with tracking
- Pattern is buried in `PlanCritic` and cannot be reused elsewhere

Desired state:
- A generic `Council` primitive that any component can use
- Two deliberation modes: `independent` (parallel blind) and `debate` (multi-round with cross-visibility)
- Consensus synthesis that *downgrades* challenges on disagreement rather than escalating them
- Per-councillor logging with color, full raw responses visible
- Structured metrics written to `_metrics/` per session

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    Council<T>                        │
│                                                      │
│  CouncilConfig      councillors: list[Councillor]    │
│                     mode: independent | debate        │
│                     debate.max_rounds: int            │
│                     debate.early_exit: bool           │
│                     consensus_threshold: float        │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │           DeliberationAdapter<T>             │   │
│  │  build_prompt(input, prior_rounds?) → str    │   │
│  │  parse_response(raw) → T                     │   │
│  │  format_for_debate(decisions) → str          │   │
│  │  synthesize(decisions, config) → T           │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  CouncilResult<T>                                    │
│    rounds: list[CouncilRound]                        │
│    final: T                                          │
│    agreement_map: dict                               │
│    synthesis_trace: list[str]                        │
│    metrics: CouncilRunMetrics                        │
└─────────────────────────────────────────────────────┘
         │
         ▼
  PlanCriticAdapter        (first consumer)
  MonitorAdapter           (future)
  EscalationAdapter        (future)
```

---

## Config Shape

```yaml
# In config.yml under runtime.plan_critic:
council:
  # Flat list — repeat providers for same-model N-times, mix for heterogeneous
  # comment: same-provider N times → variance/noise reduction (self-consistency)
  #          different providers   → epistemic independence (different training, priors)
  #          mixed N+M            → both; assign labels to track in logs/metrics
  councillors:
    - provider: anthropic
      model: null          # null = use provider default
      label: claude
    - provider: openai
      model: null
      label: gpt
    - provider: grok
      model: null
      label: grok

  mode: independent        # independent | debate

  debate:
    max_rounds: 3
    early_exit_on_consensus: true

  # Synthesis: what fraction of councillors must challenge a step to sustain full strength
  # Below threshold → downgrade one level (drop→replace, replace→justify, justify→discard)
  # Minority (≤ 1/N) → floor at justify regardless of suggestion
  consensus_threshold: 0.67
```

---

## Core Schema (`src/runtime/council.py`)

```python
@dataclass
class Councillor:
    provider: str
    model: str | None
    label: str

@dataclass
class CouncillorDecision:
    label: str
    provider: str
    model: str | None
    raw_response: str
    parsed: Any          # T — typed by the adapter
    round_number: int    # 1-indexed

@dataclass
class CouncilRound:
    round_number: int
    decisions: list[CouncillorDecision]
    converged: bool      # True if all councillors agreed this round

@dataclass
class CouncilRunMetrics:
    context: str                          # e.g. "plan_critic"
    query: str
    mode: str
    rounds_completed: int
    councillor_labels: list[str]
    per_councillor_decisions: dict        # label → parsed decision summary
    agreement_map: dict                   # per challenged item → list of agreeing labels
    synthesis_trace: list[str]
    final_verdict: str
    user_outcome: dict | None             # filled later if user is polled

@dataclass
class CouncilResult:
    rounds: list[CouncilRound]
    final: Any                            # T
    agreement_map: dict
    synthesis_trace: list[str]
    metrics: CouncilRunMetrics
```

---

## Consensus Synthesis Algorithm

Applied after all rounds complete. Input: final round's list of `CouncillorDecision`.
For the `PlanCriticAdapter`, each decision is a `CriticResult`. Generalizes to any adapter.

```
N = total councillors

for each challenged step (union across all councillors):
    challengers = [c for c in decisions if c challenged this step]
    k = len(challengers)
    ratio = k / N

    majority_suggestion = most_common(c.suggestion for c in challengers)

    if ratio >= consensus_threshold:          # e.g. 0.67 → 2/3 or 3/3
        keep suggestion at full strength
        trace: "step X: {k}/{N} challenged ({ratio:.0%} ≥ threshold) → keep {suggestion}"

    elif ratio > 1/N:                         # minority but not lone wolf
        downgrade one level:
            drop    → replace
            replace → justify
            justify → (discard this challenge)
        trace: "step X: {k}/{N} challenged ({ratio:.0%} < threshold) → downgrade to {new}"

    else:                                     # lone wolf (only 1 of N > 2)
        floor at justify
        trace: "step X: 1/{N} challenged (lone wolf) → floor at justify"

steps not challenged by anyone → implicitly approved
if no challenges survive → verdict: approved
```

This is fully deterministic — no additional LLM call for synthesis.

---

## Debate Mode Flow

```
Round 1: identical to independent mode — all councillors generate blind, in parallel
         → CouncilRound(round=1, decisions=[...])
         → check convergence: all decisions identical? → early exit if early_exit_on_consensus=True

Round 2..max_rounds:
    for each councillor:
        prompt = adapter.build_prompt(input, prior_rounds=[round1, ...])
        # prior_rounds includes ALL other councillors' full responses from all prior rounds
        # councillor can see: what they said, what others said, and are asked to reconsider
    → CouncilRound(round=N, decisions=[...])
    → check convergence again

After max_rounds or convergence:
    run synthesis algorithm on final round's decisions
```

Debate prompt extension (added by `format_for_debate`):
```
--- Prior Round Responses ---
[claude, round 1]: <full raw response>
[gpt, round 1]:    <full raw response>
[grok, round 1]:   <full raw response>

You have seen the above responses from other councillors.
Reconsider your position. If their arguments are sound, you may revise.
If you still disagree, explain specifically why. Return the same JSON format.
```

---

## Color Logger (`src/logger.py` extension)

Two formatters: `ColoredFormatter` for console (TTY + no `NO_COLOR` env), plain `Formatter` for file.

Color palette:
```
RESET       = \033[0m
DIM         = \033[2m
BOLD        = \033[1m

USER        = \033[96m   # bright cyan
ASSISTANT   = \033[92m   # bright green
RUNTIME     = \033[2m    # dim (system noise)
ERROR_BLOCK = \033[91m   # bright red
COUNCIL_HDR = \033[93m   # bright yellow  (council banners)
SYNTHESIS   = \033[1m    # bold white      (final consensus)
ESCALATE    = \033[33m   # yellow

# Per-councillor — assigned by index in councillors list, consistent per session
COUNCILLOR_PALETTE = [
    \033[34m   # blue     (index 0)
    \033[35m   # magenta  (index 1)
    \033[33m   # yellow   (index 2)
    \033[36m   # cyan     (index 3)
    \033[32m   # green    (index 4)
]
```

Logger emits the source tag in the message prefix:
```
[claude]  step 2 [bash_exec]: drop — strings reveals no information ...
[gpt]     step 2 [bash_exec]: justify — strings may reveal embedded ...
[grok]    step 2 [bash_exec]: drop — file_info already covers ...
[synth]   step 2: 2/3 challenged → downgrade drop → replace
```

TTY check: `sys.stdout.isatty() and os.environ.get("NO_COLOR") is None`

---

## Metrics Persistence (`src/runtime/council_metrics.py`)

Written to `_metrics/<session_id>.jsonl` — one JSON object per line, one entry per council run.

```json
{
  "ts": "2026-04-18T14:32:01Z",
  "session_id": "SES...",
  "context": "plan_critic",
  "query": "analyze the binary at _tests/proc",
  "mode": "independent",
  "rounds_completed": 1,
  "councillors": ["claude", "gpt", "grok"],
  "decisions": {
    "claude":  {"verdict": "challenged", "challenges": [{"step": 2, "tool": "strings", "suggestion": "drop"}]},
    "gpt":     {"verdict": "approved"},
    "grok":    {"verdict": "challenged", "challenges": [{"step": 2, "tool": "strings", "suggestion": "drop"}]}
  },
  "agreement_map": {
    "step_2": {"challengers": ["claude", "grok"], "approvers": ["gpt"], "ratio": 0.67}
  },
  "synthesis_trace": [
    "step 2: 2/3 challenged (67% ≥ threshold 0.67) → keep drop"
  ],
  "final_verdict": "challenged",
  "final_challenges": [{"step": 2, "tool": "strings", "suggestion": "drop"}],
  "user_outcome": null
}
```

`user_outcome` is filled in by the escalation gate when the user is polled:
```json
"user_outcome": {
  "user_action": "approved_step",
  "sided_with": ["gpt"],
  "overrode": ["claude", "grok"]
}
```

`CouncilMetricsWriter` is a singleton per session, initialized in `configure_logging()`.
Exposes: `record_run(metrics: CouncilRunMetrics)` and `record_user_outcome(run_id, outcome)`.

---

## Phases

### Phase 1 — Core Council primitive + independent mode
**Files**: `src/runtime/council.py` (new), `src/runtime/council_metrics.py` (new), `src/config.py`, `config.yml`

- Define all dataclasses: `Councillor`, `CouncillorDecision`, `CouncilRound`, `CouncilResult`, `CouncilRunMetrics`
- Define `DeliberationAdapter` abstract base (build_prompt, parse_response, format_for_debate, synthesize)
- Implement `Council.deliberate()` for independent mode: parallel dispatch via `asyncio.gather` or sequential fallback
- Implement consensus synthesis algorithm (deterministic, no LLM)
- Add council config to `config.yml` + `ExecutionMonitorConfig` / new `CouncilConfig` dataclass
- `CouncilMetricsWriter` — opens session JSONL file, `record_run()` method
- No integration yet — unit-testable in isolation

### Phase 2 — Debate mode
**Files**: `src/runtime/council.py`

- Add round management loop to `Council.deliberate()`
- Implement convergence check (all decisions identical for all steps)
- `format_for_debate()` default implementation in base adapter
- Respect `max_rounds` and `early_exit_on_consensus` config
- Log each round clearly: `[Round 2/3]` headers with councillor labels colored

### Phase 3 — Color logger
**Files**: `src/logger.py`

- `ColoredFormatter` class with ANSI support
- TTY + NO_COLOR detection
- `get_councillor_color(label: str) -> str` — maps label to palette index (consistent per process lifetime)
- Dual handler setup: colored stream handler + plain file handler
- Tag helpers: `council_tag(label)`, `synth_tag()`, `user_tag()`, `assistant_tag()` — return colored prefix strings for use in log messages

### Phase 4 — Metrics writer + user outcome tracking
**Files**: `src/runtime/council_metrics.py`, `src/runtime/escalation.py`, `src/logger.py`

- Wire `CouncilMetricsWriter` into `configure_logging()` — singleton accessible via `get_metrics_writer()`
- Add `run_id` to `CouncilRunMetrics` (uuid4)
- Hook `CLIUserGate.prompt()` to call `metrics_writer.record_user_outcome()` when the council attached a `run_id` to the escalation context
- Escalation schema gets optional `council_run_id: str | None`

### Phase 5 — PlanCritic integration
**Files**: `src/runtime/critic.py`, `src/runtime/prompts.py`

- Implement `PlanCriticAdapter(DeliberationAdapter[CriticResult])`
  - `build_prompt()` wraps existing `_build_user_turn()`
  - `parse_response()` wraps existing `_parse()`
  - `synthesize()` implements the downgrade algorithm over `CriticResult` objects
- Replace `_single_review` + `_merge_results` with `Council(PlanCriticAdapter).deliberate(plan)`
- Pass `council_run_id` into any resulting escalation so user outcome can be tracked
- Verify via logs: per-councillor raw responses visible, synthesis trace logged, challenge text untruncated

---

## What This Unlocks Later

- **Monitor as council**: `ExecutionMonitor.assess()` could run 2-3 models to decide retry/continue — avoids a single model confidently returning "continue" when the step silently failed
- **Escalation gate as council**: before prompting the user, poll N models on whether escalation is warranted — reduces false-positive escalations
- **Intent classification as council**: ambiguous queries ("analyze this" — plan or direct?) get a committee vote

These are not in scope for this work — the goal is to build `Council` generically enough that adding a new adapter is the only cost.

---

## Open Questions

1. **Async vs sequential dispatch**: Python async adds complexity. For now, sequential dispatch is fine — the latency difference is LLM call time not Python overhead. Can revisit when N > 3.

2. **Synthesis LLM call**: the paper uses a 4th model for synthesis. We use algorithmic synthesis. This is cheaper and more predictable for structured JSON outputs. If debate mode fails to converge on nuanced cases, an LLM synthesizer could be added as a config option later.

3. **Debate mode prompt anchoring**: there's a risk councillors anchor to the first response they see and stop reasoning independently. Mitigations: randomize the order other responses are shown, or show summaries rather than full responses. Flag for Phase 2 implementation.
