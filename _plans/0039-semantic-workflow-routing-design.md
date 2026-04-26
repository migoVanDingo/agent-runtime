# Semantic Workflow Routing

## Problem

`WorkflowMatcher` uses regex patterns. Natural language variations that don't match fall through to the LLM planner and produce inferior plans. For example, "create a c program exactly like it" doesn't match the `DeepDisassembly` pattern even though it is clearly a reverse engineering request. Adding more patterns doesn't scale — there will always be gaps.

## Solution

Two-layer semantic routing on top of existing regex:

1. **Option 1 — Classifier hint**: Extend the existing classifier call to return a `workflow` field alongside `mode`/`risk`. Zero extra latency — same LLM call.
2. **Option 3 — Targeted fallback**: If classifier and regex both return nothing and `mode=plan`, one cheap focused call: "does this match a workflow?" Only fires on genuine gaps.

Workflow descriptions stay in sync automatically — each `Workflow` class declares its own `intent` description, injected into prompts at runtime from `ALL_WORKFLOWS`. Adding a new workflow automatically makes it visible to the routing system.

## Routing Decision Tree

```
user message
    │
    ▼
IntentClassifier  →  (mode, risk, workflow_hint?)
    │
    ├─ workflow_hint = "deep-disassembly"  ──→  use DeepDisassembly
    │
    ├─ workflow_hint = null
    │       │
    │       ▼
    │   WorkflowMatcher (regex — unchanged)
    │       │
    │       ├─ match  ──→  use matched workflow
    │       │
    │       └─ no match + mode=plan
    │               │
    │               ▼
    │           WorkflowSelector (targeted LLM call)
    │               │
    │               ├─ match  ──→  use matched workflow
    │               └─ null   ──→  LLM planner (current behavior)
    │
    └─ mode=direct  ──→  _run_loop (unchanged)
```

## Key Design Decisions

**Auto-sync via `intent` property**: Each `Workflow` subclass declares an `intent: str` — 1–2 sentences describing what user requests it handles, written for an LLM audience. `WorkflowMatcher.get_descriptions()` iterates `ALL_WORKFLOWS` and collects `(name, intent)` pairs at call time. Classifier and fallback prompts use this. Adding a workflow → auto-appears everywhere.

**False positive guard**: When the classifier returns a hint, `workflow.try_match(message)` is still called as a sanity check. If it returns `None`, the hint is used anyway but logged as an unconfirmed hint. The sanity check is informational, not a gate — regex patterns are deliberately narrow.

**Fallback scope**: Option 3 only fires when `mode=plan`, classifier hint is `None`, and regex returned `None`. Never fires in direct mode. One call, not a loop.

**ClassifierResult dataclass**: `classify()` currently returns `(mode, risk)` tuple. Change to `ClassifierResult` dataclass — cleaner, forward-compatible, avoids 3-tuple unpacking everywhere.

## Files Affected

| File | Change |
|---|---|
| `workflows/base.py` | Add abstract `intent` property |
| `workflows/templates.py` | Implement `intent` on each workflow |
| `workflows/matcher.py` | Add `get_descriptions()`, `get_by_name()` |
| `runtime/schema.py` | Add `ClassifierResult` dataclass |
| `runtime/classifier.py` | Extend schema + prompt, return `ClassifierResult` |
| `runtime/prompts.py` | Add `WORKFLOW_SELECTOR_PROMPT` |
| `agent.py` | Consume `workflow_hint`, add fallback call, update routing logic |

## Phases

### Phase 1 — Workflow self-description
Add `intent` to `Workflow` base and all templates. Add `get_descriptions()` and `get_by_name()` to `WorkflowMatcher`. Pure additive — no behavior change.

### Phase 2 — ClassifierResult type
Add `ClassifierResult(mode, risk, workflow_hint)` to `runtime/schema.py`. Update `classify()` and all call sites. `workflow_hint` always `None` at this point — no behavior change.

### Phase 3 — Classifier workflow hint (Option 1)
Inject workflow descriptions into classifier prompt at call time. Extend JSON schema to include `workflow: str | null`. Populate `workflow_hint` in `ClassifierResult`. Wire into `agent.py`: if hint set, resolve via `get_by_name()` and use. Log routing path.

### Phase 4 — Targeted fallback (Option 3)
Add `WORKFLOW_SELECTOR_PROMPT` to `runtime/prompts.py`. Add `WorkflowSelector` to `runtime/classifier.py`. Wire into `agent.py`: fires only when `mode=plan`, hint=`None`, regex=`None`. Log routing path.

### Phase 5 — Observability
Log which routing path fired for every plan-mode request: `classifier_hint` / `regex` / `fallback` / `planner`. Makes it easy to audit routing accuracy in session logs.
