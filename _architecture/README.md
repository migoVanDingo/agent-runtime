# arc — architecture docs

These documents describe the design and invariants of the arc agent
runtime. They're meant to outlive any single feature plan in `_plans/`
— when something changes in a way that affects the architecture, update
the relevant doc here.

## What lives here

| Doc | Scope |
|---|---|
| [00-overview.md](00-overview.md) | One-page overview of the whole system. Read first. |
| [01-runtime-as-god.md](01-runtime-as-god.md) | The foundational tenet: who owns control flow, why tools/skills are passive. |
| [02-pipeline-and-stages.md](02-pipeline-and-stages.md) | The agent's request-processing pipeline, what each stage does, ordering constraints. |
| [03-context-discipline.md](03-context-discipline.md) | How arc bounds context per LLM call: AFM strategy, scope-aware budgets, system-prompt awareness. |
| [04-subagent-dispatch.md](04-subagent-dispatch.md) | Sub-agent spec/runner/lifecycle, isolation guarantees, no-recursion rules. |
| [05-telemetry-and-logging.md](05-telemetry-and-logging.md) | Event bus, JSONL schema v2, scope tagging, replay/export. |
| [06-tool-and-skill-extensibility.md](06-tool-and-skill-extensibility.md) | Plugin system, built-in tool/skill registries, sub-agent specs. |
| [07-storage-and-paths.md](07-storage-and-paths.md) | ARC_HOME, session paths, artifact store, RAG, blob paging. |

## What does NOT live here

- **Feature plans** (`_plans/0083`, `0087`, `0090`, …) — those describe
  the change *as designed* at one point in time. They become historical
  artifacts once shipped.
- **Implementation notes** (`_plans/0090a-implementation.md` etc.) —
  per-phase records of what actually shipped. Useful for tracking
  drift between plan and reality.
- **User-facing docs** (`README.md`) — installation, commands,
  quickstart, config.
- **API reference** — there isn't one yet; if it shows up, it goes
  next to the docstrings.

## When to update an architecture doc

- A new feature plan changes a documented invariant (e.g., "sub-agents
  may not recurse" gets relaxed in 0094 — update doc 04).
- A doc no longer matches reality (refactor moved things around).
- A new architectural concept emerges (e.g., 0090 introduced
  `runtime.scope`, which warranted a section in doc 03).

When a plan ships, ask: "did this change an invariant or add a new
load-bearing concept?" If yes, update the corresponding doc.
