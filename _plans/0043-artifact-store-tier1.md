# 0043 — Artifact Store Tier 1: In-Session State

## Scope

Tier 1 is the in-session artifact store — a named, typed registry of values
produced by tools during a single agent session. It persists to SQLite at
session end but does not yet implement cross-session loading, decay, session
resumption, or RAG. Those are Tier 2 and 3.

Six phases:

| Phase | What |
|-------|------|
| 1 | `ArtifactStore` class + SQLite schema |
| 2 | Wire into `agent.py` + `main.py` |
| 3 | Wire `read_url` — quarantine as artifact |
| 4 | `data` toolset — dataframe + data processing tools |
| 5 | `artifacts` toolset — LLM-facing CRUD tools |
| 6 | Plan schema `produces` field |

---

## Phase 1 — ArtifactStore Class

**New file:** `src/runtime/artifact_store.py`

The store follows the same singleton pattern as `TokenTracker` — a
module-level instance accessed via `get_artifact_store()`. This means tools
can import and use it without being injected with a reference, same as the
token tracker.

### SQLite schema (Tier 1 tables only)

```sql
CREATE TABLE IF NOT EXISTS artifacts (
    key           TEXT    PRIMARY KEY,
    kind          TEXT    NOT NULL,
    value         TEXT,
    summary       TEXT,
    source        TEXT    DEFAULT '',
    data_path     TEXT,
    session_id    TEXT    NOT NULL,
    created_at    REAL    NOT NULL,
    last_accessed REAL    NOT NULL,
    access_count  INTEGER NOT NULL DEFAULT 0,
    decay_score   REAL    NOT NULL DEFAULT 1.0,
    permanent     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS artifact_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    key         TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    accessed_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT    PRIMARY KEY,
    started_at     REAL    NOT NULL,
    ended_at       REAL,
    artifact_count INTEGER DEFAULT 0,
    resumable      INTEGER DEFAULT 1
);
```

Tier 2/3 columns (`summary_embedding`, `conversation_history`, `requests`,
etc.) are not created yet. The schema is forward-compatible — later phases
add columns and tables via `ALTER TABLE` and `CREATE TABLE IF NOT EXISTS`
without breaking existing data.

### ArtifactMeta dataclass

```python
@dataclass
class ArtifactMeta:
    key:           str
    kind:          str
    summary:       str
    source:        str
    session_id:    str
    created_at:    float
    last_accessed: float
    access_count:  int
    decay_score:   float
    permanent:     bool
    has_value:     bool       # inline value present
    has_data_path: bool       # large file reference present
```

### ArtifactStore class

```python
class ArtifactStore:
    def __init__(self, db_path: Path, data_dir: Path): ...

    def init_session(self, session_id: str) -> None: ...
    def flush(self) -> None: ...

    def set(self, key: str, value, kind: str, source: str = "") -> ArtifactMeta: ...
    def get(self, key: str) -> Any: ...
    def meta(self, key: str) -> ArtifactMeta | None: ...
    def list(self, kind: str | None = None) -> list[ArtifactMeta]: ...
    def expel(self, key: str) -> bool: ...
    def expel_pattern(self, pattern: str) -> list[str]: ...
    def pin(self, key: str) -> None: ...
```

**`init_session(session_id)`** — called at agent startup. Registers the session
in the `sessions` table. Sets the active session ID on the store. In Tier 1
this is all it does — no prior session loading yet.

**`flush()`** — called at agent shutdown. Writes all in-memory dirty artifacts
to SQLite. Writes `artifact_sessions` audit rows. Updates `sessions.ended_at`
to NULL (detached/resumable — Tier 2 will use this).

**`set(key, value, kind, source)`** — stores a value under a name. Routing:
- `kind == "dataframe"` → write parquet to `_store/data/<key>.parquet`,
  store `data_path`, generate `summary` (shape + columns + dtypes + 3 rows)
- serialized size ≤ `inline_threshold` → store inline in `value` column
- serialized size > threshold → write to `_store/data/<key>.<ext>`,
  store `data_path`, generate `summary` (char count + first 300 chars)

**`get(key)`** — returns the value. Checks in-memory cache first. If not
cached: if `value` is set, deserialize and return; if `data_path` is set,
load from file (parquet → DataFrame, text/json → string/dict). Updates
`last_accessed` and `access_count`.

**`expel(key)`** — removes from in-memory cache and SQLite. If `data_path`
exists, deletes the file from disk. Logs the expel to `artifact_sessions`.
Returns True if found and removed, False if key didn't exist.

**`expel_pattern(pattern)`** — expels all keys matching a glob pattern
(e.g. `"fetched_*"`). Returns list of expelled keys.

### Storage routing detail

```python
INLINE_THRESHOLD = 4096  # bytes

kind_extensions = {
    "url_content": "txt",
    "result":      "txt",
    "string":      "txt",
    "file":        "txt",
    "path":        "txt",
}
# default fallback: .json

def _summary_for_df(df) -> str:
    cols = ", ".join(f"{c} ({df[c].dtype})" for c in df.columns)
    sample = df.head(3).to_string(index=False)
    return f"shape={df.shape}  columns=[{cols}]\n{sample}"

def _summary_for_text(text: str) -> str:
    return f"{len(text):,} chars\n{text[:300]}"
```

### Module-level singleton

```python
_store: ArtifactStore | None = None

def get_artifact_store() -> ArtifactStore:
    if _store is None:
        raise RuntimeError("ArtifactStore not initialized — call init_store() first")
    return _store

def init_store(db_path: Path, data_dir: Path) -> ArtifactStore:
    global _store
    _store = ArtifactStore(db_path, data_dir)
    return _store
```

### Files created
- `src/runtime/artifact_store.py`
- `_store/` directory (created at first run)
- `_store/data/` directory (created at first run)

---

## Phase 2 — Wire into agent.py and main.py

### `main.py` changes

The session ID is already generated in `main.py`. It needs to flow to the
artifact store before `Agent` is constructed.

```python
from runtime.artifact_store import init_store, get_artifact_store
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

session_id = generate_id("session")
configure_logging(session_id, verbose=args.verbose)

# Initialize store before Agent (tools may use it during construction)
store = init_store(
    db_path=PROJECT_ROOT / "_store" / "artifacts.db",
    data_dir=PROJECT_ROOT / "_store" / "data",
)
store.init_session(session_id)

agent = Agent(verbose=args.verbose)
```

On exit (both `KeyboardInterrupt` and `quit`):
```python
get_artifact_store().flush()
get_tracker().log_summary()
```

### `agent.py` changes

None required for Phase 2. The store is a module-level singleton — tools
call `get_artifact_store()` directly. No injection needed.

### `.gitignore` update

`_store/` should not be committed (contains user data, potentially large
parquet files). Add:
```
_store/
```

### Config additions (`config.yml`)

```yaml
artifact_store:
  enabled: true
  inline_threshold_bytes: 4096
```

`db_path` and `data_dir` are derived from the project root at runtime, not
config — they don't change and aren't user-facing.

### Files modified
- `src/main.py`
- `config.yml`
- `.gitignore`

---

## Phase 3 — Wire read_url

`read_url` currently:
1. Writes content to `/tmp/agent_fetch_<hash>.txt`
2. Returns the path as a string in the tool result

After Phase 3:
1. Writes content to `/tmp/agent_fetch_<hash>.txt` (unchanged — quarantine
   temp file remains for the injection gate logic)
2. On passing inspection: copies to `_store/data/fetched_<hash>.txt`,
   registers as artifact `fetched_<hash>`, returns the artifact key
3. Injection gate `expel` path calls `store.expel(f"fetched_{hash}")` which
   deletes `_store/data/fetched_<hash>.txt` — one call, complete cleanup

### Tool result format (after)

```
Fetched: https://arxiv.org/abs/2604.21928
Artifact: fetched_a3f92b1c04
Size: 12,450 chars
Title/first line: [2604.21928] Scaling Inference-Time Compute...
Preview: This paper investigates the trade-offs between...

Use read_file_lines on the artifact path, or get_artifact 'fetched_a3f92b1c04'
to read the content.
```

The agent reads the file via `read_file` using the `data_path` from
`store.meta("fetched_a3f92b1c04").has_data_path` — or the new `get_artifact`
tool (Phase 5). Either works.

### Injection gate update

The injection gate in `DirectExecutionStage` and `ExecutionStage` currently
parses the quarantine path from the result string with a regex. After Phase 3,
the artifact key is in the result string instead. `expel(key)` handles cleanup.

The temp file in `/tmp/` is still written (needed for the gate logic since it
runs before the artifact is registered). After the gate passes, the content
moves to `_store/data/`. The `/tmp/` file can then be deleted.

### Files modified
- `src/tools/implementations/web/read_url.py`
- `src/runtime/stages/direct_execution.py` (injection gate expel path)
- `src/runtime/stages/execution.py` (injection gate expel path)

---

## Phase 4 — data Toolset

**New directory:** `src/tools/implementations/data/`

Six tools. All pure Python — no new system dependencies beyond `pandas` and
`jinja2` (both common, adding to `requirements.txt`).

### `dataframe_load`

Loads a file into a named dataframe artifact.

Inputs: `source` (file path or artifact key), `name` (artifact key to store
as), `format` (csv/json/tsv/parquet — auto-detected if omitted)

Supported formats: CSV, TSV, JSON (records or lines), JSON-normalized (nested
→ flat via `pd.json_normalize`), Parquet, HTML tables (via BeautifulSoup,
already a dep).

Returns: artifact key + summary (shape, columns, dtypes, 3-row preview).

```python
df = pd.read_csv(source)
store.set(name, df, kind="dataframe", source=source)
return f"Loaded as artifact '{name}'\n{store.meta(name).summary}"
```

### `dataframe_query`

Runs a pandas expression against one or more loaded dataframe artifacts.

Inputs: `expression` (pandas expression string), `dataframes` (dict mapping
name → artifact key, e.g. `{"df": "sales_data"}`), `output` (optional
artifact key to store result), `format` (table/csv/json for return format)

The expression is evaluated in a restricted context — only the named
dataframes and `pd` are in scope. No builtins, no os, no subprocess.

```python
context = {"pd": pd}
for alias, key in dataframes.items():
    context[alias] = store.get(key)

result = eval(expression, {"__builtins__": {}}, context)
```

Returns: result as formatted string (table, CSV, or JSON). If `output` is
set, also stores the result as a new artifact.

Guard: ESCALATE. The `expression` field is code execution — the user should
approve it. The approval cache key is `dataframe_query:<expression>` so
repeated identical queries don't re-prompt.

### `json_query`

JSONPath extraction from a JSON file, artifact, or inline JSON string.

Inputs: `source` (file path, artifact key, or JSON string), `path` (JSONPath
expression e.g. `$.store.books[*].title`)

Dependency: `jsonpath-ng` (lightweight, no C extensions).

Returns: matched values as a formatted list.

### `regex_match`

Find/extract regex patterns in a file, artifact, or string.

Inputs: `source` (file path, artifact key, or raw string), `pattern` (regex),
`mode` (find/extract/replace), `flags` (i/m/s), `output` (optional artifact
key for results)

Returns: matches with line numbers and context (2 lines before/after).

### `diff_files`

Unified diff between two files or artifacts.

Inputs: `a` (file path or artifact key), `b` (file path or artifact key),
`context_lines` (default 3), `output` (optional file to write diff)

Dependency: stdlib `difflib`. Zero new deps.

Returns: unified diff string.

### `template_render`

Render a Jinja2 template with provided variables.

Inputs: `template` (template string or file path), `variables` (dict),
`output` (optional file to write result)

Dependency: `jinja2`.

Returns: rendered string, or confirmation that output was written.

### Toolset definition

```python
DATA = Toolset(
    name="data",
    description="Data processing — dataframes, JSON queries, regex, diffs, templates",
    planning_note=(
        "Use dataframe_load to load CSV/JSON/TSV into a named dataframe artifact. "
        "Use dataframe_query to filter, aggregate, join, or transform loaded dataframes "
        "using pandas expressions. Use json_query for JSONPath extraction. "
        "Use regex_match to extract patterns from text. "
        "Use diff_files to compare two files or artifacts."
    ),
    tools=[
        DataframeLoadTool(),
        DataframeQueryTool(),
        JsonQueryTool(),
        RegexMatchTool(),
        DiffFilesTool(),
        TemplateRenderTool(),
    ],
    rules=[
        RoutingRule(toolset="data", condition=any_keyword(
            "dataframe", "csv", "tsv", "pandas", "dataframe",
            "json path", "jsonpath", "filter", "aggregate", "groupby",
            "join", "merge", "pivot", "regex", "pattern", "extract",
            "diff", "compare files", "template", "render", "jinja",
        )),
        RoutingRule(toolset="data", condition=has_extension(
            ".csv", ".tsv", ".parquet", ".jsonl",
        )),
    ],
)
```

`ActionType` gains `DATA = "data"` and the JSON schema enum is updated.
`PLANNING_SYSTEM_PROMPT` picks it up automatically via `build_tool_list()`.

### Guard additions

`dataframe_query` → ESCALATE (eval of user-provided expression).
Approval key: `dataframe_query:<expression>`.

All other data tools → ALLOW (read-only or file writes in working dir).

### New dependencies (`requirements.txt`)

```
pandas>=2.0.0
pyarrow>=14.0.0   # parquet support for pandas
jsonpath-ng>=1.6.0
jinja2>=3.1.0
```

### Files created
- `src/tools/implementations/data/__init__.py`
- `src/tools/implementations/data/dataframe_load.py`
- `src/tools/implementations/data/dataframe_query.py`
- `src/tools/implementations/data/json_query.py`
- `src/tools/implementations/data/regex_match.py`
- `src/tools/implementations/data/diff_files.py`
- `src/tools/implementations/data/template_render.py`

### Files modified
- `src/tools/toolsets.py` (add DATA toolset + imports)
- `src/planning/schema.py` (add `ActionType.DATA`)
- `src/runtime/guard.py` (dataframe_query ESCALATE)
- `requirements.txt`

---

## Phase 5 — artifacts Toolset

**New directory:** `src/tools/implementations/artifacts/`

LLM-facing wrappers over `ArtifactStore` methods. Thin — each tool is
~30 lines delegating to the store.

### Tools

**`list_artifacts`**
No required inputs. Optional `kind` filter.
Returns formatted table: key | kind | source | summary | last_accessed.

**`get_artifact`**
Input: `key`. Returns the artifact value as a string. For dataframes returns
the CSV representation (truncated at 200 rows with a note if larger).
Handles inline values and file-backed values transparently.

**`store_artifact`**
Inputs: `key`, `value` (string), `kind` (default "string"), `source` (optional).
Manually registers a value as a named artifact. Useful when the LLM wants to
preserve an intermediate result for later steps.

**`expel_artifact`**
Input: `key`. Removes the artifact and any associated files.
Returns confirmation or "not found".

**`artifact_info`**
Input: `key`. Returns `ArtifactMeta` fields as formatted text — does not load
the value. Useful when the LLM wants to check if an artifact exists and see
its summary without paying the cost of loading a large dataframe.

### Toolset definition

```python
ARTIFACTS = Toolset(
    name="artifacts",
    description="Manage named artifacts — store, retrieve, inspect, and delete session values",
    planning_note=(
        "Use list_artifacts to see what's available from prior steps. "
        "Use get_artifact to retrieve a stored value by name. "
        "Use store_artifact to save an intermediate result you'll need later. "
        "Use expel_artifact to delete a value and free its storage."
    ),
    tools=[
        ListArtifactsTool(),
        GetArtifactTool(),
        StoreArtifactTool(),
        ExpelArtifactTool(),
        ArtifactInfoTool(),
    ],
    rules=[
        RoutingRule(toolset="artifacts", condition=any_keyword(
            "artifact", "stored", "store result", "save result",
            "what's available", "list artifacts", "recall artifact",
        )),
    ],
)
```

All artifact tools → ALLOW. They are read/write against the store, not the
filesystem or network.

`expel_artifact` is the exception — it deletes files. It should be ESCALATE.
Approval key: `expel_artifact:<key>`.

### Files created
- `src/tools/implementations/artifacts/__init__.py`
- `src/tools/implementations/artifacts/list_artifacts.py`
- `src/tools/implementations/artifacts/get_artifact.py`
- `src/tools/implementations/artifacts/store_artifact.py`
- `src/tools/implementations/artifacts/expel_artifact.py`
- `src/tools/implementations/artifacts/artifact_info.py`

### Files modified
- `src/tools/toolsets.py` (add ARTIFACTS toolset + imports)
- `src/runtime/guard.py` (expel_artifact ESCALATE)

---

## Phase 6 — Plan Schema `produces` Field

The `Step` dataclass and JSON schema gain an optional `produces` field — the
artifact key the step is expected to register in the store.

### Schema change

```python
# planning/schema.py
@dataclass
class Step:
    step:        int
    description: str
    action_type: ActionType
    tool:        str | None = None
    produces:    str | None = None    # new — optional artifact key
    status:      StepStatus = StepStatus.PENDING
    ...
```

JSON schema enum gains `"produces": {"type": ["string", "null"]}`.

This is non-breaking: existing plans without `produces` parse fine. The
field defaults to `None`.

### Executor behavior

After a step completes in `ExecutionStage`, if `step.produces` is set:
check whether that key now exists in the store. If not, log a warning:
```
  ⚠ step declared produces='paper_content' but artifact was not registered
```

The monitor receives this as a flag. It can use it as additional signal for
a retry or replan decision — but it doesn't hard-fail the step. Some tools
will register artifacts without a `produces` declaration; the field is
advisory, not a contract.

### Planner prompt update

The `PLANNING_USER_TURN` example for `read_url` gains `"produces"`:

```json
{
  "step": 1,
  "description": "Fetch the page at https://arxiv.org/abs/2604.21928",
  "action_type": "web",
  "tool": "read_url",
  "produces": "paper_content",
  "flags": {"retry": false, "escalate": false, "defer": false}
}
```

A note is added to the planning system prompt:
> Optional "produces" field: if a step stores a result as a named artifact,
> set "produces" to the artifact key. Later steps can reference it by name.

### Files modified
- `src/planning/schema.py`
- `src/planning/prompts.py`
- `src/runtime/stages/execution.py` (post-step produces check + warning)

---

## What Tier 1 Does Not Include

These are explicitly out of scope for Tier 1 and belong to Tier 2:

- Cross-session artifact loading (loading prior sessions' artifacts at startup)
- Session resumption (`--resume` flag, `conversation_history` table)
- Decay scoring applied across sessions
- Workflow discovery clustering
- The `requests` table
- Any Tier 3 (embeddings, RAG, sqlite-vec)

The `decay_score` column is created in the schema (Phase 1) but is never
decremented in Tier 1. It starts at 1.0 and stays there. Tier 2 adds the
decay pass at session start.

---

## Dependency Summary

| Dependency | Phase | Already present? |
|-----------|-------|-----------------|
| `sqlite3` | 1 | Yes — stdlib |
| `pandas` | 4 | No — add to requirements.txt |
| `pyarrow` | 4 | No — add to requirements.txt |
| `jsonpath-ng` | 4 | No — add to requirements.txt |
| `jinja2` | 4 | No — add to requirements.txt |

---

## File Change Summary

| File | Phase | Change |
|------|-------|--------|
| `src/runtime/artifact_store.py` | 1 | New |
| `src/main.py` | 2 | init_store, flush on exit |
| `config.yml` | 2 | artifact_store config block |
| `.gitignore` | 2 | add `_store/` |
| `src/tools/implementations/web/read_url.py` | 3 | register artifact, return key |
| `src/runtime/stages/direct_execution.py` | 3 | expel via store |
| `src/runtime/stages/execution.py` | 3 | expel via store |
| `src/tools/implementations/data/` | 4 | New directory + 6 tools |
| `src/tools/toolsets.py` | 4, 5 | DATA + ARTIFACTS toolsets |
| `src/planning/schema.py` | 4, 6 | ActionType.DATA, produces field |
| `src/runtime/guard.py` | 4, 5 | dataframe_query + expel_artifact ESCALATE |
| `requirements.txt` | 4 | pandas, pyarrow, jsonpath-ng, jinja2 |
| `src/tools/implementations/artifacts/` | 5 | New directory + 5 tools |
| `src/planning/prompts.py` | 6 | produces field note + example update |
| `src/runtime/stages/execution.py` | 6 | produces check + warning |
