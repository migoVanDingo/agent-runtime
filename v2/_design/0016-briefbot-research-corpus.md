# 0016 — Briefbot research-corpus integration

## Motivation

Briefbot is an external nightly-indexed SQLite corpus of AI/ML papers,
blog posts, HN/Lobsters items, and dev-tools/security news that runs on
the user's machine.  v1 wired it in with three tools — `briefbot_search`,
`briefbot_item`, `briefbot_trending` — backed by a SQLModel DAL that
points at the Briefbot SQLite via `BRIEFBOT_DB_PATH`.  The agent uses
these constantly: "what's the state of <tech>", "find a paper about X",
"summarize this week's tooling activity."

v2 deliberately ships with **no persistence layer**.  Each session is a
self-contained directory; there's no SQLModel, no Alembic, no agent DB.
Briefbot doesn't need any of that — it's **read-only** and lives in its
own file.  The integration is straightforward: open the SQLite file at
session start, expose three tools, close at session end.  No schema, no
migrations, no shared state with arc.

This phase ports v1's three tools into v2's tool architecture and adds the
one missing extension point — **a plugin that contributes tools** — so the
DB handle has a clear owner and lifecycle.

Out of scope for this phase: porting v1's RAG layer (LanceDB-based,
operates on session chunks).  v2 has no session chunks to embed; a RAG
phase would need to design that ingestion path first.  Sketched in §10.

---

## Scope

In:
- New plugin `arc.plugins.briefbot` that:
  - Opens the Briefbot SQLite read-only in `on_session_start`
  - Owns three tool *instances* bound to the DB handle
  - Closes the connection in `on_session_end`
  - Quarantines cleanly if the DB is missing/unreadable (tools simply
    not registered; session continues without them)
- New extension point: **plugins can contribute tools** via an optional
  `provides_tools() -> list[Tool]` method.  The tool registry merges
  plugin-contributed tools with the configured `tools.enabled` list.
- Three tools: `briefbot_search`, `briefbot_item`, `briefbot_trending`
- Read-only SQLite open with `?mode=ro&uri=true`
- New event types for the plugin's own observability
- Optional: opt-in default in `defaults.py` (plugin enabled but loads
  lazily; absent DB = clean no-op)

Out (deferred):
- RAG over session chunks (v1's `LocalRagService` + LanceDB).  Needs its
  own design phase; see §10.
- Briefbot ingestion path — fully external; arc remains a read-only
  consumer.
- Cross-session learning / persistent user history of "interesting
  items".  Could live in a future `~/.arc/briefbot_notes.jsonl` file
  with its own plugin.
- Briefbot tool calls feeding back into a vector index (would happen as
  part of the deferred RAG phase).

---

## Architecture

```
src/arc/plugins/briefbot/
  __init__.py
  plugin.py              ← Lifecycle owner; opens/closes DB
  dal.py                 ← Three small DAL classes (Items, Clusters, Topics)
  tools/
    briefbot_search.py
    briefbot_item.py
    briefbot_trending.py
tests/unit/test_briefbot_plugin.py
tests/unit/test_briefbot_dal.py
tests/unit/test_briefbot_tools.py
tests/integration/test_briefbot_live.py   # skips if BRIEFBOT_DB_PATH unset
```

### The plugin

```python
class BriefbotPlugin:
    name = "briefbot"

    def __init__(self, *, db_path: Path | None, bus=None):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._bus = bus
        self._tools: list[Tool] = []

    def bind_bus(self, bus): self._bus = bus

    def on_session_start(self, ctx: SessionContext) -> None:
        if self._db_path is None or not self._db_path.exists():
            self._emit("disabled", {"reason": "BRIEFBOT_DB_PATH not set or file missing",
                                    "path": str(self._db_path) if self._db_path else None})
            return
        self._conn = sqlite3.connect(
            f"file:{self._db_path}?mode=ro&immutable=1",
            uri=True, check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        items_dal = ItemsDAL(self._conn)
        clusters_dal = ClustersDAL(self._conn)
        topics_dal = TopicsDAL(self._conn)
        self._tools = [
            BriefbotSearchTool(items_dal),
            BriefbotItemTool(items_dal),
            BriefbotTrendingTool(clusters_dal, topics_dal),
        ]
        self._emit("ready", {"path": str(self._db_path),
                             "item_count": items_dal.count(),
                             "tools": [t.name for t in self._tools]})

    def on_session_end(self, ctx: SessionContext) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def provides_tools(self) -> list[Tool]:
        return list(self._tools)
```

### The new `provides_tools()` extension point

This is a small but real new contract on plugins.  Today plugins extend
behavior via the 12 hooks; tools come from a separate `_BUILDERS` dict
in `arc/tools/__init__.py`.  For tools that need session-scoped state
(an open DB handle, a connection pool, a cached model), the cleanest
ownership is a plugin that builds them.

Implementation in `runtime/loop.py` / wherever the tool registry is built:

```python
# Build tools from tools.enabled (existing path)
tools = build_tools(cfg.tools)

# Then merge plugin-contributed tools
for built in plugins:
    instance = built.instance
    if hasattr(instance, "provides_tools"):
        # on_session_start has already fired by this point; tools are bound
        for t in instance.provides_tools():
            if t.name in tools:
                raise ValueError(
                    f"plugin {built.name!r} provides tool {t.name!r} "
                    f"but a tool with that name is already registered"
                )
            tools[t.name] = t
```

Naming collisions raise loudly — silent override is the worst outcome.

Tools contributed by plugins are *not* listed in `tools.enabled`.  They're
implicit: enabling the plugin enables its tools.  Users who want to
disable a single Briefbot tool while keeping the others can do so via the
plugin's own config (`disabled_tools: [briefbot_trending]`) — see §4.

---

## DAL design

Three small classes, sync sqlite3 (not aiosqlite — v2's loop is sync).
Each DAL takes a `sqlite3.Connection`; returns plain dicts (not ORM
models — v2 has no ORM).

```python
class ItemsDAL:
    def __init__(self, conn): self._c = conn

    def count(self) -> int: ...

    def search(self, *, query: str, days: int, category: str | None,
               limit: int, order_by: str) -> list[dict]: ...

    def get_by_id(self, item_id: str) -> dict | None: ...

class ClustersDAL:
    def get_trending(self, *, window: str, limit: int) -> list[dict]: ...

class TopicsDAL:
    def get_top_topics(self, *, window: str, limit: int) -> list[dict]: ...
```

SQL is the same v1 uses — LIKE on title+summary for items, velocity_*
ordering for clusters, momentum ordering for topics.  See v1
`src/db/dal/briefbot/*` for the queries; lift them verbatim and drop the
SQLModel wrapping.

Schema version awareness: query `PRAGMA user_version` (or
`SELECT MAX(version) FROM schema_migrations` if Briefbot uses Alembic)
once at open.  If the version is below a known-good floor, emit a
`briefbot.schema_mismatch` event and disable the tools.  We don't track
schema versions in the arc tree; just refuse to operate against schemas
that lack expected columns.

---

## Tool surface (for the model)

### `briefbot_search`

```
name:        briefbot_search
description: Search the local Briefbot research corpus (papers, blog
             posts, HN/Lobsters, tooling and security news, refreshed
             nightly). Use briefbot_item to drill into a result.
input:       query       (string, required)
             days        (int, default 30, recency window)
             category    (enum, optional: ai_research|papers|ai_industry|
                          devtools|mlops_infra|security|tech_news|aggregator)
             limit       (int, default 15, max 50)
             order_by    (enum: 'score'|'date', default 'score')
output:      ranked list:
               [1] (score=87) Title — source
                   https://url
                   summary excerpt...
                   opportunity: ...
```

### `briefbot_item`

```
name:        briefbot_item
description: Fetch full details for a Briefbot item by id.
input:       item_id (string, required)
output:      title, url, source, author, score/score_opportunity, tags,
             published_at/fetched_at, opportunity_reason, summary
```

### `briefbot_trending`

```
name:        briefbot_trending
description: Trending story clusters and hot topics in the corpus
             (no query needed). Use to discover what to read about.
input:       window         (enum: '1d'|'3d'|'7d', default '3d')
             clusters_limit (int, default 8)
             topics_limit   (int, default 10)
output:      Trending clusters (label, trend_score, velocity_*, item_count,
             representative title+URL) + hot topics (name, kind, momentum,
             counts across windows)
```

Output formatting follows v1's conventions — leading rank/score, then
URL, then summary.  Easy for the model to parse and quote.

---

## Config

```yaml
plugins:
  enabled:
    # ... existing ...
    - name: briefbot
      enabled: true                       # disable to skip even probing the DB
      config:
        db_path: null                     # null = read $BRIEFBOT_DB_PATH env var
        disabled_tools: []                # e.g., [briefbot_trending] to drop one
        search_default_days: 30
        search_default_limit: 15
        trending_default_window: "3d"
        max_summary_chars: 1200           # truncate per-item summary in tool output
      hooks_order:
        on_session_start: 30              # after recorder (10), before log_writer (5)
        on_session_end: 30
```

Resolution order for `db_path`:
1. `config.plugins.briefbot.config.db_path` if set
2. `BRIEFBOT_DB_PATH` env var if set
3. `~/.briefbot/briefbot.db` if it exists (the upstream default)
4. None → plugin emits `briefbot.disabled` and does nothing

This matches the v1 pattern but moves it into the plugin (v1 had it in a
global settings module).

---

## Observability

```
EventType.BRIEFBOT_READY         "briefbot.ready"
  { path, item_count, tools }

EventType.BRIEFBOT_DISABLED      "briefbot.disabled"
  { reason, path }

EventType.BRIEFBOT_SCHEMA_MISMATCH "briefbot.schema_mismatch"
  { path, expected, found }

EventType.BRIEFBOT_QUERY         "briefbot.query"
  { tool, query, params, result_count, took_ms }
```

Last event emits per tool call.  Standard tool events
(`tool.call.started/completed`) cover input + output.  `briefbot.query`
adds the structured query parameters and `took_ms` for performance
visibility, which doesn't naturally fit in the tool's returned string.

Log-writer formatters:

```
📚 briefbot: ready (12,847 items at ~/.briefbot/briefbot.db)
📚 briefbot: disabled (BRIEFBOT_DB_PATH not set)
🔎 briefbot_search "ghidra" (cat=devtools, days=30) → 8 results in 14ms
```

---

## Recovery and failure modes

| Failure | Behavior |
|---|---|
| `db_path` unresolved or file missing | Plugin emits `briefbot.disabled`; `provides_tools` returns `[]`; session continues normally. |
| DB file present but unreadable | `sqlite3.OperationalError` → plugin emits `briefbot.disabled` with the reason; same outcome (no tools, session continues). |
| Schema mismatch (missing column) | `briefbot.schema_mismatch` event; tools not registered. |
| Tool execution raises (corrupt row, encoding) | Tool raises `ToolError` with the underlying message; model sees it, runtime's tool-cycle detector handles retry storms. |
| Briefbot ingestor mid-write (rare with WAL) | `?mode=ro&immutable=1` rejects writers; if the file is genuinely being rewritten, surface as a transient `ToolError("briefbot DB locked; try again")`. |
| Empty result set | Tool returns `"No results for '<query>' in last <days>d"` as success — not an error. |
| Plugin construction raises | Standard plugin-quarantine path; arc continues without Briefbot. |

The "disabled, continue" path is deliberate.  Briefbot is *additive*; not
having it shouldn't kill a session.

---

## Provides-tools timing

`on_session_start` runs first (priority 30 above), and the runtime needs
the tool registry built **before** the first turn.  Concretely:

```
1. Build plugins (constructor)
2. Register plugins with bus
3. Build tools (existing path, from tools.enabled)
4. Fire on_session_start for each plugin
5. Merge plugin-contributed tools into the registry
6. First user turn starts; tools are available
```

Step 5 happens after step 4 because `provides_tools()` returns the bound
tools that were constructed in `on_session_start`.  Tool list is captured
into the `LLMRequest.tools` shape at each turn's start, so the merged
registry is what every turn sees.

---

## RAG follow-up (sketch only)

v1's RAG layer indexes session chunks (final summaries + Ghidra/file
analyses) into LanceDB, then injects retrieved historical context into
new sessions.  Porting it to v2 needs four pieces:

1. **What to chunk.**  v2 has events.jsonl per session — the natural
   source.  An indexer would walk `~/.arc/sessions/*/events.jsonl`,
   extract messages and tool outputs, chunk, embed.
2. **Where the index lives.**  `~/.arc/rag/` with LanceDB tables.
3. **When to index.**  An `on_session_end` plugin that walks the just-
   finished session's events and updates the index.  Or a separate
   `arc rag reindex` CLI.
4. **How retrieval slots in.**  A `pack_context` plugin that retrieves
   top-K relevant chunks for the current user input and injects them as
   a system-prompt addendum (or a leading user message).

The pluggable-context phase the user already has memos on (0089 in v1's
notes) is the obvious umbrella — RAG is one strategy among several.
This phase is the foundation, not the RAG itself.

When that phase lands, Briefbot RAG would mean: index Briefbot items
into the same LanceDB store with a `source: "briefbot"` tag, retrieve
during sessions when relevant.  No coupling required at this stage — the
read-only-DB tool path coexists cleanly with a future vector path.

---

## File layout

```
src/arc/plugins/briefbot/
  __init__.py
  plugin.py
  dal.py
  tools/
    __init__.py
    briefbot_search.py
    briefbot_item.py
    briefbot_trending.py
tests/unit/test_briefbot_plugin.py
tests/unit/test_briefbot_dal.py
tests/unit/test_briefbot_tools.py
tests/integration/test_briefbot_live.py
tests/fixtures/briefbot/mini.db        ← tiny sqlite for unit tests
```

Plus:
- `src/arc/plugins/__init__.py` — `_build_briefbot` + `_BUILDERS` entry
- `src/arc/runtime/events.py` — four new EventType constants
- `src/arc/plugins/log_writer/formatter.py` — formatters for each
- `src/arc/defaults.py` — `briefbot` entry under `plugins.enabled`
  (enabled by default; gracefully no-ops without the DB)
- Runtime tool-registry merge step (the `provides_tools` integration)

No new top-level deps — sqlite3 is stdlib.

---

## Test plan

Unit (`test_briefbot_dal.py`, against `tests/fixtures/briefbot/mini.db`):
1. `ItemsDAL.search` — query match, days filter, category filter, limit,
   order_by score/date
2. `ItemsDAL.get_by_id` — hit + miss
3. `ClustersDAL.get_trending` — per-window ordering
4. `TopicsDAL.get_top_topics` — momentum ordering

Unit (`test_briefbot_tools.py`):
1. Each tool's `input_schema` — required/optional fields
2. Each tool's `execute` against an in-memory DAL — well-formed output,
   empty-result string, max-summary-chars truncation
3. Tool collision (two plugins offering the same tool name) → registry
   raises at startup

Unit (`test_briefbot_plugin.py`):
1. Resolution order for `db_path` (config > env > default path > None)
2. Missing DB → `briefbot.disabled`, `provides_tools` = []
3. Present DB → `briefbot.ready` event with item count and tool list
4. `disabled_tools` config drops named tools from `provides_tools`
5. `on_session_end` closes the connection
6. Schema mismatch path emits `briefbot.schema_mismatch` and disables
7. Plugin failure during open is quarantined (session does not crash)

Integration (`test_briefbot_live.py`):
1. Skip unless `BRIEFBOT_DB_PATH` is set and the file exists
2. Run a turn that calls `briefbot_search`, assert non-empty result
3. Run a turn that calls `briefbot_trending`, assert clusters returned

Smoke:
- `arc bootstrap`; ensure `briefbot` is in default config
- Set `BRIEFBOT_DB_PATH=~/.briefbot/briefbot.db`
- `arc run "any interesting AI papers this week?"` — confirms tool path
- Without the env var: same command runs, tool absent, agent answers
  honestly about not having Briefbot available

---

## Why this is a plugin, not just three tools

Three reasons:

1. **Lifecycle ownership.**  The SQLite connection wants to open once at
   session start and close at session end.  Tools alone don't have
   session lifecycle hooks; plugins do (`on_session_start` /
   `on_session_end`).
2. **Graceful absence.**  Without the DB, the tools shouldn't exist at
   all (rather than existing and always erroring).  Plugins can return
   `[]` from `provides_tools()`; tools can't refuse to register
   themselves.
3. **Observability.**  Plugin-level events (`briefbot.ready`,
   `briefbot.disabled`) tell the user *why* the tools are or aren't
   available — separate from per-call observability.

The `provides_tools()` extension point is small and generalizable: any
future plugin that owns session-scoped resources (a connection pool, a
loaded model, a cached index) can use the same pattern.

---

## State

Planned.
