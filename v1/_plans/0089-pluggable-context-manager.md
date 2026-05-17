# 0089 — Pluggable context manager

> **Audience:** Implementer with full codebase access, no prior context.
> Read `_plans/0085-file-length-audit.md` §6 first — that plan decomposes
> the current monolithic `ContextManager` into modules, which is a
> prerequisite for the work here. Then read this doc end-to-end.
>
> **Reading order:** `0079-runtime-as-god.md` → `0085` §6 (split prerequisite)
> → this doc → relevant phase doc.

---

## 0. Goal

Make context-management strategies **swappable at config-time** so research
into packing approaches doesn't require a code edit. Today,
`ContextManager` (`src/runtime/context_manager.py`, 522 lines) is the
single hardcoded strategy: AFM-inspired non-destructive packing with
similarity + recency + importance scoring and FULL / COMPRESSED /
PLACEHOLDER fidelity tiers.

Future strategies the user wants to be able to plug in without rewriting
callers:

- **Pure token-budget truncation** — drop oldest under budget. Simple, fast,
  baseline.
- **AFM-style non-destructive packing** — current strategy (kept as
  default).
- **Sliding window with summary** — keep last N messages verbatim; everything
  older becomes a single summary message.
- **Retrieval-augmented packing** — pack only the messages whose RAG
  embeddings score above threshold for the current query.
- **Hybrid** — combinations of the above.

The boundary is `pack(messages, query, plan_start_index) -> list[dict]`.
Everything else is per-strategy state.

---

## 1. What `ContextManager` does today (algorithm summary)

Verified against `src/runtime/context_manager.py:53–522`:

1. **Quick check**: if total token estimate ≤ budget, return unchanged.
2. **Score** each message via `_score_messages` (lines 122–184):
   - Lazy-load shared embedding model.
   - Compute query embedding (if model available).
   - Per message: classify importance (rule-based + LLM overrides);
     compute semantic similarity to query (cosine); compute recency decay
     (exponential half-life). Combine via tier-specific weights.
   - CRITICAL messages always get score 1.0.
   - Plan-active messages get importance boosted to HIGH.
3. **Assign fidelity** (`_assign_fidelity`, 225–259):
   - score ≥ `threshold_high` → FULL
   - score ≥ `threshold_mid` → COMPRESSED
   - otherwise → PLACEHOLDER
   - Plan-active tool results forced to FULL; other plan-active forced ≥ COMPRESSED.
4. **Pack chronologically** (`_pack_chronological`, 261–357):
   - Build pair-links: assistant-with-tool_use ↔ user-with-tool_result must
     stay paired (Anthropic API constraint).
   - Normalize pair fidelities to the lower of the two.
   - Walk in order. For each message, try `_try_fit` at intended fidelity;
     downgrade if over budget; drop if even placeholder doesn't fit.
   - If one half of a pair fits but the other doesn't, drop both.
5. **`_try_fit`** (359–387): tries FULL → COMPRESSED → PLACEHOLDER,
   returns the best that fits in remaining budget.
6. **Compression** (`_compress_message`, 389–437): per-role compression.
   For tool results, optionally route through LLM summarizer via
   `_compress_tool_result` (439–474, uses cache).
7. **Placeholder** (`_placeholder_message`, 476–522): builds API-valid
   stubs that preserve tool_use IDs and tool_result IDs.

Also maintains:

- `_importance_overrides: dict[int, Importance]` — set by `ImportanceScorer`
  via `set_importance(msg_index, importance)`.
- `_summary_cache: dict[str, str]` — LLM-summary memoization.
- `_summarizer: BaseProvider` — set once via `set_summarizer(provider)`.

---

## 2. Call-site inventory

`ContextManager.pack(messages, query, plan_start_index=None)` is called
from:

| Caller | File:line | Notes |
|---|---|---|
| `RoutingStage` | `runtime/stages/routing.py` (verify line) | For classification context |
| `ExecutionStage` | (indirect — via `ToolLoop`) | per ReAct iteration |
| `DirectExecutionStage` | (indirect — via `ToolLoop`) | per ReAct iteration |
| `ToolLoop.run` | `runtime/tool_loop.py` | before every provider.chat |

Plus:

- `ImportanceScorer` calls `context_mgr.set_importance(msg_index, imp)` per
  scored step (`runtime/stages/execution.py:304`).
- `Agent.__init__` calls `context_mgr.set_summarizer(runtime_provider)`
  (`agent.py:144`).

The boundary is small and stable. Pluggability is tractable.

---

## 3. Design

### 3.1 Strategy protocol

`src/runtime/context/strategy.py` (new file — relies on 0085-§6 split):

```python
from __future__ import annotations
from typing import Protocol, runtime_checkable
from runtime.schema import Importance


@runtime_checkable
class ContextStrategy(Protocol):
    """Decides what messages to include before each provider.chat() call.

    All methods are call-time pure where possible. State (e.g. caches,
    overrides) lives inside the implementation.
    """

    name: str  # stable identifier — matches the config key, e.g. "afm"

    def pack(
        self,
        messages: list[dict],
        current_query: str,
        plan_start_index: int | None = None,
    ) -> list[dict]:
        """Return a budget-constrained message list."""
        ...

    # ── Optional capabilities — strategies may no-op these ──────────

    def set_summarizer(self, provider) -> None: ...
    def set_importance(self, message_index: int, importance: Importance) -> None: ...
    def get_importance(self, message_index: int) -> Importance | None: ...
```

The current `ContextManager` becomes the `"afm"` strategy implementation.
Other strategies implement only `pack` plus whatever optional methods
apply.

For strategies that don't track per-message importance, `set_importance` is
a no-op and `get_importance` returns `None`. ImportanceScorer's calls
become best-effort.

### 3.2 Strategy registry / factory

`src/runtime/context/factory.py` (new):

```python
from runtime.context.strategy import ContextStrategy
from runtime.context.manager import ContextManager  # the AFM strategy
from runtime.context.strategies.truncation import TruncationStrategy
from runtime.context.strategies.sliding import SlidingWindowStrategy
from runtime.context.strategies.rag_aug import RagAugmentedStrategy
from app_config import config


_REGISTRY: dict[str, type] = {
    "afm": ContextManager,
    "truncate": TruncationStrategy,
    "sliding": SlidingWindowStrategy,
    "rag": RagAugmentedStrategy,
}


def build_strategy(name: str | None = None) -> ContextStrategy:
    """Construct the strategy named in config.runtime.context.strategy."""
    cfg = config.runtime.context
    chosen = (name or cfg.strategy or "afm").lower()
    cls = _REGISTRY.get(chosen)
    if cls is None:
        raise ValueError(f"unknown context strategy: {chosen!r} "
                         f"(known: {sorted(_REGISTRY)})")
    return cls(params=cfg.params.get(chosen, {}))


def register_strategy(name: str, cls: type) -> None:
    """Plugin escape hatch for adding strategies at runtime."""
    _REGISTRY[name] = cls
```

The `register_strategy` API is the integration point with the plugin system
(plan 0088). A future entry-point group `arc.context_strategies` could
populate the registry.

### 3.3 Configuration

New top-level config block, replacing the current
`ContextManagerConfig`'s implicit "this is the only strategy":

```yaml
# config.yml
runtime:
  context:
    strategy: afm           # afm | truncate | sliding | rag
    params:
      afm:
        enabled: true
        message_budget_tokens: 30000
        half_life_turns: 6
        threshold_high: 0.6
        threshold_mid: 0.3
        compressed_max_chars: 400
      truncate:
        budget_tokens: 30000
        keep_first_user: true
      sliding:
        budget_tokens: 30000
        keep_last_n: 20
        summarize_older: true
      rag:
        budget_tokens: 30000
        per_strategy_k: 12
        score_threshold: 0.65
```

`src/config.py` (or its post-0085 split):

```python
@dataclass
class ContextConfig:
    strategy: str = "afm"
    params: dict[str, dict] = field(default_factory=dict)
```

Replace `RuntimeConfig.context_manager: ContextManagerConfig` with
`RuntimeConfig.context: ContextConfig`. Old config layout (
`runtime.context_manager.message_budget_tokens` etc.) continues to work
via a compat shim in the loader (`_load_context_config_with_compat`) for
one release.

### 3.4 Constructor signature

Each strategy class accepts a single `params: dict` (passed by the factory).
The strategy parses what it needs and ignores the rest. This gives plugins
free param surface without changing the abstract base.

```python
class TruncationStrategy:
    name = "truncate"

    def __init__(self, params: dict) -> None:
        self._budget = int(params.get("budget_tokens", 30000))
        self._keep_first_user = bool(params.get("keep_first_user", True))

    def pack(self, messages, current_query, plan_start_index=None):
        # ... simple oldest-out-first packing ...

    def set_summarizer(self, provider): pass
    def set_importance(self, message_index, importance): pass
    def get_importance(self, message_index): return None
```

### 3.5 Wiring in Agent

Replace `agent.py:143`:

```python
self.context_mgr = ContextManager()
self.context_mgr.set_summarizer(get_runtime_provider())
```

with:

```python
from runtime.context.factory import build_strategy
self.context_mgr = build_strategy()
self.context_mgr.set_summarizer(get_runtime_provider())
```

`self.context_mgr` is now typed `ContextStrategy`. Existing callers
(`ToolLoop`, `RoutingStage`, etc.) only call `pack()` plus the four
optional methods — all part of the Protocol.

### 3.6 Telemetry integration (cross-references 0087)

Per 0087-§6.4, every `pack()` call emits two events:

- `context.pack.started` — input size, budget, strategy name
- `context.pack.completed` — output size, decisions made, duration

The base class doesn't emit; instrumentation lives at the call sites in the
stages (so a strategy implementation can't be skipped accidentally). The
strategy *name* is fetched from `strategy.name`.

The `payload.strategy` field on these events is what enables the analyst to
compare strategies in pandas.

---

## 4. File layout

After 0085-§6 lands (`runtime/context/` exists), this plan adds:

```
src/runtime/context/
├── strategy.py               ~50 lines — ContextStrategy Protocol
├── factory.py                ~60 lines — registry + build_strategy + register_strategy
├── manager.py                (existing — becomes the "afm" strategy; gets a `name = "afm"` attr)
├── scoring.py                (existing per 0085-§6)
├── fidelity.py               (existing per 0085-§6)
├── packing.py                (existing per 0085-§6)
├── compression.py            (existing per 0085-§6)
└── strategies/
    ├── __init__.py
    ├── truncation.py         ~80 lines — TruncationStrategy
    ├── sliding.py            ~140 lines — SlidingWindowStrategy
    └── rag_aug.py            ~180 lines — RagAugmentedStrategy
```

---

## 5. The new strategies

### 5.1 TruncationStrategy (~80 lines)

Drop oldest messages until under budget. Preserve the first user message
(task definition). Preserve tool_use/tool_result pairs atomically (drop
both or keep both).

```python
class TruncationStrategy:
    name = "truncate"

    def __init__(self, params: dict) -> None:
        self._budget = int(params.get("budget_tokens", 30000))
        self._keep_first_user = bool(params.get("keep_first_user", True))

    def pack(self, messages, current_query, plan_start_index=None):
        if not messages:
            return messages
        total = _sum_tokens(messages)
        if total <= self._budget:
            return messages

        # Identify pair partners (same logic as AFM)
        pairs = _detect_tool_pairs(messages)

        # Walk newest → oldest; keep messages until budget exhausted.
        # Always keep the first user message if configured.
        keep = set()
        budget = self._budget
        if self._keep_first_user:
            for i, m in enumerate(messages):
                if m["role"] == "user" and isinstance(m["content"], str):
                    keep.add(i)
                    budget -= _est_tokens(m["content"])
                    break

        for i in range(len(messages) - 1, -1, -1):
            if i in keep:
                continue
            cost = _est_tokens(_message_text(messages[i]))
            # Account for pair partner if present
            if i in pairs:
                cost += _est_tokens(_message_text(messages[pairs[i]]))
                if cost <= budget:
                    keep.add(i)
                    keep.add(pairs[i])
                    budget -= cost
            else:
                if cost <= budget:
                    keep.add(i)
                    budget -= cost

        return [m for i, m in enumerate(messages) if i in keep]
```

Pair-link detection helper from `runtime/context/packing.py` (per
0085-§6) should be exported so strategies can share it.

### 5.2 SlidingWindowStrategy (~140 lines)

Keep last N messages verbatim. Older messages get a single LLM-generated
summary message inserted in their place (cached).

```python
class SlidingWindowStrategy:
    name = "sliding"

    def __init__(self, params: dict) -> None:
        self._budget = int(params.get("budget_tokens", 30000))
        self._keep_last_n = int(params.get("keep_last_n", 20))
        self._summarize = bool(params.get("summarize_older", True))
        self._summarizer = None  # set via set_summarizer
        self._summary_cache: dict[str, str] = {}

    def set_summarizer(self, provider) -> None:
        self._summarizer = provider

    def pack(self, messages, current_query, plan_start_index=None):
        if len(messages) <= self._keep_last_n:
            return messages

        older = messages[:-self._keep_last_n]
        recent = messages[-self._keep_last_n:]
        # Adjust window forward to keep tool pairs intact (don't slice between halves)
        recent = _expand_to_pair_boundary(older, recent)

        if not self._summarize or self._summarizer is None:
            return recent

        # Build or fetch summary
        key = _digest(older)
        if key not in self._summary_cache:
            self._summary_cache[key] = self._summarize_messages(older)

        prelude = {"role": "user", "content":
                   f"Summary of earlier conversation:\n{self._summary_cache[key]}"}
        return [prelude] + recent

    def _summarize_messages(self, messages) -> str:
        # one LLM call via self._summarizer; falls back to mechanical
        # join if call fails
        ...

    def set_importance(self, *_): pass
    def get_importance(self, *_): return None
```

### 5.3 RagAugmentedStrategy (~180 lines)

Pack only the messages whose embeddings score above threshold against the
current query (semantic relevance). Always keep the last K messages
verbatim for immediate context.

```python
class RagAugmentedStrategy:
    name = "rag"

    def __init__(self, params: dict) -> None:
        self._budget = int(params.get("budget_tokens", 30000))
        self._k = int(params.get("per_strategy_k", 12))
        self._threshold = float(params.get("score_threshold", 0.65))
        self._keep_last_n = int(params.get("keep_last_n", 8))
        self._embedding_model = None

    def pack(self, messages, current_query, plan_start_index=None):
        if not messages or not current_query:
            return messages
        if self._embedding_model is None:
            from embeddings import get_embedding_model
            self._embedding_model = get_embedding_model()

        recent = messages[-self._keep_last_n:]
        older = messages[:-self._keep_last_n]
        if not older:
            return messages

        # Embed query and older messages; pick top-K above threshold
        scored = _embed_and_score(older, current_query, self._embedding_model)
        selected_indices = [i for i, score in scored if score >= self._threshold][:self._k]

        # Reorder chronologically; preserve pair atomicity
        kept_older = [older[i] for i in sorted(selected_indices)]
        kept_older = _enforce_pair_atomicity(older, kept_older)

        # Always preserve first user message (task definition)
        first_user_idx = _find_first_user_idx(older)
        if first_user_idx is not None and older[first_user_idx] not in kept_older:
            kept_older = [older[first_user_idx]] + kept_older

        return kept_older + recent

    def set_summarizer(self, *_): pass
    def set_importance(self, *_): pass
    def get_importance(self, *_): return None
```

(The embedding logic could share code with `runtime/context/scoring.py`.)

---

## 6. Telemetry per strategy

Each strategy's `pack()` should set up enough decision-trace data that the
0087 telemetry events (emitted from the *call sites*, not the strategy) have
meaningful content:

```python
# Inside the call site, e.g., ToolLoop:
t0 = time.monotonic()
input_size = _est(messages)
packed = self._context_mgr.pack(messages, query, plan_start_index)
output_size = _est(packed)

bus.emit(RuntimeEvent(
    event_type="context.pack.completed",
    identity=...,
    duration_ms=int((time.monotonic() - t0) * 1000),
    payload={
        "strategy": self._context_mgr.name,
        "n_in": len(messages),
        "n_out": len(packed),
        "tokens_in": input_size,
        "tokens_out": output_size,
        "ratio": output_size / max(input_size, 1),
    },
))
```

Strategies that want to surface internal decisions (e.g., "I dropped 14
messages, kept 8 at FULL fidelity") may emit their own
`context.pack.detail` events. This is optional and per-strategy.

---

## 7. Phase breakdown

Phases assume 0085-§6 (`runtime/context/` decomposition) has landed. If
not, fold §1 of 0085 into 0089a.

| Phase | Title | Scope |
|---|---|---|
| **0089a** | Strategy Protocol + factory + config | `runtime/context/strategy.py`, `runtime/context/factory.py`, `config.py` |
| **0089b** | Promote current `ContextManager` to the `"afm"` strategy | `runtime/context/manager.py` (add `name="afm"`, accept `params` dict) |
| **0089c** | Wire factory into `Agent.__init__`; thread strategy through callers | `agent.py`, `runtime/tool_loop.py`, all `*Stage.__init__` that accept `context_mgr` |
| **0089d** | Implement TruncationStrategy + SlidingWindowStrategy | `runtime/context/strategies/{truncation,sliding}.py` |
| **0089e** | Implement RagAugmentedStrategy + emit pack telemetry | `runtime/context/strategies/rag_aug.py`, call-site events |

### 0089a — Protocol + factory + config

- Define `ContextStrategy` Protocol.
- Define `build_strategy(name=None)` reading from
  `config.runtime.context.strategy`.
- Add `ContextConfig` dataclass to `config.py` (or its split).
- Compat shim in loader: if `runtime.context_manager` block exists in
  yaml and `runtime.context` does not, fall back.

**Verification**: `build_strategy("afm")` returns something that satisfies
`isinstance(obj, ContextStrategy)`.

### 0089b — Promote ContextManager → afm strategy

- Add `name = "afm"` class attribute.
- Change `__init__` to accept `params: dict | None = None` and read budget
  / thresholds / half-life / compressed_max from `params` (with the same
  defaults as `ContextManagerConfig` today).
- Keep `set_summarizer` / `set_importance` / `get_importance` as today.
- `register_strategy("afm", ContextManager)` in `factory.py`.

**Verification**: `build_strategy()` returns a working
`ContextManager` instance whose `pack()` produces identical output to the
old code path on a recorded test fixture.

### 0089c — Wire factory into Agent

- `agent.py:143`: replace `ContextManager()` with `build_strategy()`.
- All places that type-hint `context_mgr: ContextManager` change to
  `context_mgr: ContextStrategy`. List:
  - `runtime/stages/routing.py`
  - `runtime/stages/execution.py`
  - `runtime/stages/direct_execution.py`
  - `runtime/tool_loop.py`
- Run the test suite. Behavior unchanged.

**Verification**: agent runs end-to-end with default config → identical
to today.

### 0089d — Truncation + Sliding

- Implement `TruncationStrategy` per §5.1.
- Implement `SlidingWindowStrategy` per §5.2.
- Add to factory registry.
- Tests: for a 50-message conversation, each strategy returns a valid
  message list of expected length; pair atomicity preserved.

**Verification**: switch `config.yml` to
`runtime.context.strategy: truncate`, restart agent, run a 5-turn
conversation. Verify telemetry shows strategy=truncate in
`context.pack.completed`.

### 0089e — RagAugmented + telemetry

- Implement `RagAugmentedStrategy` per §5.3.
- Wire `context.pack.started`/`context.pack.completed` events at the four
  call sites (this work overlaps with 0087-§6.4 — coordinate ordering).

**Verification**: switch to `strategy: rag`, run a session; verify
relevant earlier messages survive packing even when 50 messages back.

---

## 8. Backwards compatibility

- Existing config (`runtime.context_manager.message_budget_tokens` …) keeps
  working via the loader shim for one release. Log a deprecation hint:
  `runtime.context_manager.*` → use `runtime.context.params.afm.*`.
- `ContextManager` class name remains; it's now also the canonical "afm"
  strategy. Existing imports work.
- All four call sites continue to call `self._context_mgr.pack(...)`. No
  signature change.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Two strategies produce incompatible message shapes (e.g., one inserts a "summary" message that breaks tool-pair invariants) | Test suite at the boundary — any strategy whose output breaks Anthropic's tool_use/tool_result invariant fails CI |
| Default-strategy regression | 0089b round-trips the AFM strategy on a recorded fixture; no behavior change shipped |
| Plugin strategy makes load-bearing decisions about retry (drift) | Strategies are passive packers; `register_strategy` requires the implementation to satisfy `ContextStrategy`. Plugin loading at 0088 should NOT permit registering strategies that subclass internals — but that's a 0088 concern (plugin sandboxing). |
| ImportanceScorer's `set_importance` becomes a no-op under non-AFM strategies | Documented; ImportanceScorer wraps calls in try/except and continues |
| Multiple strategies share embedding model state in different ways | Each strategy lazy-loads via `get_embedding_model()` which is process-singleton — no conflict |

---

## 10. Open questions

**Q1**. Should strategies be allowed to *mutate* the messages they return,
or must they return new dicts? Recommend new dicts. The Messenger remains
the source of truth; strategies produce views.

**Q2**. Should `pack()` be async to allow strategies that hit a remote
service? Today it's sync. Recommend keeping sync — async would ripple
through `ToolLoop` and `provider.chat`. A network-bound strategy can run
on a thread pool inside its own `pack()`.

**Q3**. Should strategy switching be runtime-mutable (e.g., the agent
escalates to a more expensive strategy mid-session)? Out of scope. Config-
time only.

**Q4**. Naming: `afm`, `truncate`, `sliding`, `rag` — is "afm" too jargon-y
for the default? Recommend keep — it's the historical name; renaming
breaks anyone with custom configs. Add an alias `"default"` → `"afm"`.

**Q5**. Should the strategy be told `plan_start_index` even if it can't use
it? Yes — keep the boundary stable. Strategies that don't care ignore it.

**Q6**. Should plan 0088 (plugins) expose a registration entry point for
context strategies? Recommend yes — `arc.context_strategies` entry-point
group. Add to 0088 spec or as a 0088 follow-up.

---

## 11. Verification — end-to-end

After all phases land:

1. `config.yml` with default `strategy: afm` → behavior identical to today.
   Pin via a recorded test fixture: pack a known 30-message conversation
   and compare against a stored expected output.
2. Switch to `strategy: truncate`. Run a session. Verify
   `context.pack.completed` events show `strategy: "truncate"`.
3. Switch to `strategy: sliding`. Verify older messages collapse into a
   summary.
4. Switch to `strategy: rag`. Verify relevant earlier messages survive
   even after many turns; irrelevant ones drop.
5. Invalid strategy name in config → clear error at startup, not at first
   call.

---

## 12. Reading order for the implementer

1. `_plans/0079-runtime-as-god.md` — confirms strategies are passive.
2. `_plans/0085-file-length-audit.md` §6 — prerequisite decomposition.
3. This document.
4. The phase doc currently being executed.

If 0085-§6 hasn't landed yet, do its scope first; otherwise the current
522-line `context_manager.py` becomes 522 + factory + protocol + new
strategies ≈ ungainly.
