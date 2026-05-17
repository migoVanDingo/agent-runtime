# 03 — Context discipline

How arc bounds the size of each LLM call so per-minute rate limits and
context windows don't surprise users in long sessions.

## The problem

Conversation history accumulates across turns. Tool results, RAG-injected
chunks, analysis manifests — each adds to the system prompt or the
message stream. Without active bounding, a long session's routing call
(against a small classifier-class model) can grow to 100k+ tokens and
exceed the runtime provider's per-minute rate limit. The 119k-token
routing call in session `SES01KRRZQY3GPYX8D3WCMD54936K` motivated the
0090 work.

## The mechanism

Three coordinating pieces:

### 1. Pluggable context strategies (0089)

`runtime.context.strategy.ContextStrategy` is a Protocol. Strategies
decide what messages to send before each `provider.chat()` call. Four
built-ins:

| Name | Mechanism |
|---|---|
| `afm` (default) | Score every message by similarity + recency + importance, assign FULL / COMPRESSED / PLACEHOLDER fidelity, pack chronologically to a token budget. Preserves tool_use/tool_result pair atomicity. |
| `truncate` | Drop oldest messages until under budget. Preserves first user message + tool pairs. |
| `sliding` | Keep last N messages verbatim; collapse older into a single summary. |
| `rag` | Pack only messages whose embeddings score above threshold for the current query. |

Plug-in: pick via `runtime.context.strategy` in `config.yml`. Each
strategy gets its parameter block via `runtime.context.params.<name>`.

### 2. Scope-aware budgets (0090a)

`runtime.scope.current_scope()` returns one of:

- `"main"` — default, the user-facing agent loop
- `"runtime"` — a classifier-style stage (`RoutingStage`,
  `SkillHintStage`, `ExecutionMonitor`, `ImportanceScorer`) about to
  call the runtime provider
- `"subagent:<name>"` — a child agent currently executing

Stages enter the appropriate scope via `with scoped(RUNTIME):` /
`with scoped("subagent:foo"):`. The scope is process-wide
(via `contextvars`) so nested calls inherit it automatically.

AFM reads the scope on every `pack()` call and selects the budget:

| Scope | Budget config | Default |
|---|---|---|
| `runtime` | `runtime_message_budget_tokens` | 12000 |
| anything else | `message_budget_tokens` | 65536 |

The smaller runtime budget is the load-bearing fix for haiku-class
classifier calls — even with a long conversation, AFM packs to ~12k
tokens before sending to the runtime provider, well under the typical
50k/min rate limit.

### 3. System-prompt-aware packing (0090a)

`pack()` accepts a keyword-only `system_prompt_size: int = 0`. AFM
computes:

```python
effective_budget = max(1000, total_budget - system_prompt_size)
```

So the messages pack to whatever's left after the system prompt is
accounted for. Stages that know their system-prompt size pass it
(routing in particular); others get the default 0 and behave as before.

When `system_prompt_size > 50% of total_budget`, AFM emits a warning —
the messages will be packed aggressively, which usually means the
system prompt is the right place to look for bloat.

## Bounded system-prompt growth (0090b)

The system prompt isn't packed by AFM, so its size must be bounded
elsewhere:

- **Analysis manifest cap.** `session_paths.build_analysis_manifest()`
  enforces both a count cap (default 20 entries) and a char cap
  (default 4000). The char cap is what prevents silent growth across
  long sessions.
- **Per-stage toolset narrowing.** Runtime-stage calls
  (routing/skill_hint/monitor/importance) send `tools=[]` — no tool
  schemas. Main-provider stages narrow to the toolsets the current
  plan step's `action_type` references.
- **RAG block char budget.** `config.rag.injection_budget_chars`
  (default 2000) caps the RAG-injected chunk block at ~500 tokens.
  Only injected into main-provider stages, never runtime-stage system
  prompts.

## The full call sequence

When `RoutingStage.run()` fires:

```
1. enter scope("runtime")
2. build routing_system from config.agent.system_prompt + skill descriptions
3. compute estimate_tokens(routing_system)  →  sys_size
4. context_mgr.pack(messages, query, system_prompt_size=sys_size)
   AFM:
     - reads current_scope() → "runtime"
     - picks runtime_message_budget_tokens (12000)
     - effective_budget = 12000 - sys_size
     - if total messages > effective_budget: pack
5. provider.chat(messages=packed, system=routing_system, tools=[])
6. exit scope (restores to whatever was outside)
```

Total LLM call: `len(packed) + sys_size ≤ total_budget`, regardless of
how big the underlying conversation history is.

## Verifying context discipline in a session

Every `context.pack.started` and `context.pack.completed` event carries:

- `scope`
- `total_budget`
- `effective_budget`
- `system_prompt_size`
- `n_messages_in` / `n_messages_out`
- `input_token_estimate` / `output_token_estimate`

So you can audit with pandas:

```python
df = pd.read_json("~/.arc/sessions/<id>/events/runtime.jsonl", lines=True)
pack_completed = df[df.event_type == "context.pack.completed"]
pack_completed.groupby("scope")[["total_budget", "output_token_estimate"]].agg(["max", "mean"])
```

If you see `output_token_estimate > effective_budget` for any row,
something is bypassing the strategy.

## Related plans

- `_plans/0089-pluggable-context-manager.md` — the strategy framework.
- `_plans/0090-context-discipline-and-subagents.md` — adds scope-awareness
  and bounded system-prompt growth.
- `_plans/0087-telemetry-overhaul.md` — defines the `context.pack.*`
  events.
