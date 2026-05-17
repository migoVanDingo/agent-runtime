# 0090b — Implementation notes

> Companion to `_plans/0090-context-discipline-and-subagents.md` §6 0090b.

## What landed (and what didn't need to)

The plan called for three sub-changes:

1. **Cap the analysis manifest** ✅ shipped
2. **Per-stage toolset narrowing** ✅ already done before 0090 — verified
3. **RAG block scaled to remaining headroom** ✅ already adequately bounded — verified, no changes needed

Investigation during 0090b showed that the system-prompt size for runtime
stages is actually small (~1.7k tokens for routing, ~300 tokens for monitor/
importance), so the dominant contributor to large LLM calls is the
conversation history, not the system prompt. 0090a already addresses that
for runtime-scope calls. The analysis manifest was the only system-prompt
component that could grow unboundedly.

## Files modified

- `src/session_paths.py:build_analysis_manifest`:
  - Signature gains `max_entries: int = 20, max_chars: int = 4000` parameters.
  - Char cap is enforced after the count cap by walking entries and stopping
    when adding the next would exceed the budget (after subtracting header,
    footer, and truncation-note slack).
  - The truncation note's "X more" count reflects what's *actually* dropped,
    whether by count or by char limit.

## Files added

- `tests/unit/test_analysis_manifest_cap.py` — 4 tests:
  - empty when no artifacts exist
  - count cap respected
  - char cap respected even when count cap would allow more
  - below both caps → everything included, no truncation note

## What was verified (and didn't need a fix)

### Per-stage toolset narrowing

Before doing any work I read the four runtime-stage call sites:

| Stage | Call site | tools= argument |
|---|---|---|
| `RoutingStage` | `runtime/stages/routing.py:75` | `tools=[]` |
| `SkillHintStage` → `WorkflowSelector.select` | `runtime/classifier.py:52` | `tools=[]` |
| `ExecutionMonitor` | `runtime/monitor.py:155` | `tools=[]` |
| `ImportanceScorer` | `runtime/importance.py:64` | `tools=[]` |

All four already send an empty `tools` array, so they never ship the 4k+
tokens of tool schemas. No change needed. Adding it to 0090b's
verification checklist as an invariant.

### RAG block scaling

Measured contributions:

- `config.rag.injection_budget_chars` = 2000 chars (~500 tokens). Bounded.
- The RAG block is *not* included in runtime-stage system prompts
  (`routing_system`, `MONITOR_SYSTEM_PROMPT`, `_IMPORTANCE_PROMPT`,
  `WORKFLOW_SELECTOR_SYSTEM_PROMPT`). It's only used in `step_prompt.py`
  (execution stages, main provider) and `direct_execution.py` (main
  provider). Main provider has higher rate limits — 500 extra tokens
  there is fine.

### System prompt sizes today

| Prompt | Chars | Tokens |
|---|---|---|
| `config.agent.system_prompt` | 2509 | 627 |
| `build_routing_system` (10 skills) | 6703 | 1675 |
| `MONITOR_SYSTEM_PROMPT` | 1249 | 312 |
| `WORKFLOW_SELECTOR_SYSTEM_PROMPT` | 1148 | 287 |
| `build_analysis_manifest()` (typical 3 artifacts) | 316 | 79 |

Routing's system prompt at ~1.7k tokens is the largest runtime-stage
overhead. Well within the 12000 runtime budget set by 0090a. The
manifest cap at 4000 chars (~1000 tokens) keeps it that way even after
hundreds of artifacts accumulate.

## Why the 119k call really happened (revised understanding)

After 0090a + 0090b investigation: the 119k was **conversation history
that AFM's old default `message_budget_tokens=65536` was being
respected against an undercount**. AFM estimates tokens as
`chars / 4`. The Anthropic tokenizer counts code-heavy content at
roughly 2x that for the same string, so a "65k token" AFM-packed
conversation can land as ~115-120k actual Anthropic tokens.

0090a's `runtime_message_budget_tokens: 12000` AFM-estimate ≈ ~24k
actual Anthropic tokens. Comfortably under haiku's 50k/min limit even
with the 2x undercount.

A future hardening would be to use the real provider tokenizer for AFM's
budget computation, but it's not strictly necessary — the runtime budget
defaults are already conservative enough. Flagged for future
consideration if telemetry shows runtime calls still going wide.

## Verification

- Compile-check: clean.
- 4 new manifest tests pass.
- Full pytest: 160 passed (+14 from 0090a/b), 9 pre-existing failures, no
  new regressions.

## What hasn't shipped yet

- The logging filter that prefixes log records with the scope tag — **0090c**.
- TUI spinner showing active scope — **0090c**.
- `agent_scope` field on runtime events — **0090c**.

## Open issues / known limitations

- The 4000-char manifest cap is a guess. Worth revisiting if users
  report missing important artifact references. Could be made
  config-driven (`config.runtime.context.params.afm.manifest_max_chars`)
  if tuning becomes a thing — for now hardcoded.
- AFM's `chars/4` token estimate undercounts by ~2x for Anthropic on
  code-heavy content. Tradeoff: cheaper than running the actual
  tokenizer, conservative enough now that runtime budget = 12000 ≈ 24k
  actual.
- No automated test that asserts "tools=[] for runtime-stage call
  sites." A code-review checklist item. Could be hardened with an AST
  check in tests/ if drift becomes a concern.
