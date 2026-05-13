# 0080 — Analysis Artifact Paging + Session Directory Consolidation

## Problems

**1. Token blowout (immediate)**
Heavy reversing tools (`ghidra_decompile`, `ghidra_find_constants`, etc.) return 50k+ character
strings directly into LLM context. A single Ghidra decompile saturates a 30k TPM budget before
the LLM can even reply → 429 rate limit errors.

**2. Context compaction (deferred)**
The agent carries analysis data in context. After a long conversation the compaction window erases
it. The user asks a follow-up and the agent has lost everything it learned.

**3. Output sprawl (operational)**
Three separate top-level directories with flat, unorganized files:
- `_logs/<session_id>.log` — session log
- `_metrics/<session_id>.jsonl` — council debate metrics
- `_events/<session_id>.jsonl` — structured runtime events

These are hardcoded in three different source files. There is no session context — everything is
a pile of identically-named files in separate silos.

---

## Target Directory Layout

```
_sessions/                              ← replaces _logs/, _metrics/, _events/
  <session_id>/
    logs/
      session.log                       ← was _logs/<session_id>.log
    metrics/
      council.jsonl                     ← was _metrics/<session_id>.jsonl
    events/
      runtime.jsonl                     ← was _events/<session_id>.jsonl

_analysis/                              ← new; project-scoped, survives across sessions
  <binary_name>/
    ghidra_decompile.txt                ← full Ghidra C pseudocode, all functions
    ghidra_decompile_main.txt           ← single-function variant
    ghidra_find_constants.txt
    r2_disassemble_main.txt
    r2_functions.txt
    pseudocode.c                        ← agent-written, human-editable
    reconstruction_v1.c                 ← first reconstruction attempt
    oracle_diff_v1.txt                  ← behavior comparison notes
    ...
```

**Why `_analysis/` is project-scoped, not session-scoped**: analysis of a binary is a
multi-session collaboration. The user and agent pick up where they left off in a new session.
Nesting it under a session ID would make prior work invisible in future sessions.

**Why `_sessions/` uses nested subdirs**: one directory per concern within a session keeps
`ls _sessions/<id>/` readable and makes it obvious what each file is without knowing the
naming convention.

---

## Phase D — Session Directory Consolidation  *(do first — everything else builds on this)*

### Central paths module

Create `src/session_paths.py` — the single source of truth for all output paths.
No other file should hardcode `_logs`, `_metrics`, or `_events`.

```python
# src/session_paths.py
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent   # project root

def session_dir(session_id: str) -> Path:
    return ROOT_DIR / "_sessions" / session_id

def log_path(session_id: str) -> Path:
    return session_dir(session_id) / "logs" / "session.log"

def metrics_path(session_id: str) -> Path:
    return session_dir(session_id) / "metrics" / "council.jsonl"

def events_dir(session_id: str) -> Path:
    return session_dir(session_id) / "events"

def analysis_dir(binary_path: str) -> Path:
    return ROOT_DIR / "_analysis" / Path(binary_path).name
```

### Changes to existing files

**`src/logger.py`**
- Remove `LOGS_DIR` constant.
- In `configure_logging(session_id)`: call `log_path(session_id)`, mkdir the parent, open the file.
- Update the banner line to show `_sessions/<session_id>/` instead of individual log path.

**`src/runtime/council_metrics.py`**
- Remove `METRICS_DIR` constant.
- In `CouncilMetricsWriter.__init__`: call `metrics_path(session_id)` from `session_paths`.
- `mkdir(parents=True, exist_ok=True)` on the parent (council metrics currently only does `exist_ok=True` on the flat dir).

**`src/runtime/events/bus.py` — `JsonlEventSink`**
- Accept `session_id` in `__init__` instead of `root: Path`.
- Derive path from `events_dir(session_id) / "runtime.jsonl"`.
- Or: keep `root: Path` but pass `events_dir(session_id)` from the caller.

**`src/runtime/events/runtime.py` — `init_runtime_events`**
- Pass `events_dir(session_id)` to `JsonlEventSink` instead of `Path(cfg.directory)`.
- `cfg.directory` becomes unused (can leave in config as an override escape hatch or remove).

**`src/config.py`**
- Change default `directory: str = "_events"` → `directory: str = ""` (now unused by default).
- Or keep it for the override case but add a note that it's only consulted when non-empty.

**`src/main.py`**
- Banner already shows the log file path — update it to show `_sessions/<session_id>/`.

### Migration note

Old `_logs/`, `_metrics/`, `_events/` directories are not deleted automatically.
They can be removed manually after verifying the new layout works.
Document this in a one-line comment in `session_paths.py`.

---

## Phase A — Tool Output Paging in ToolCallExecutor

**Depends on**: Phase D (uses `analysis_dir()` from `session_paths`)

### What changes

`src/runtime/tool_executor.py` — add `_maybe_page(tool, result, tool_input) -> str`:

```python
PAGE_THRESHOLD_CHARS = 8_000   # ~2k tokens

def _maybe_page(tool: BaseTool, result: str, tool_input: dict) -> str:
    if tool.weight != ToolWeight.HEAVY and len(result) <= PAGE_THRESHOLD_CHARS:
        return result   # small enough to stay in context

    binary_path = tool_input.get("path", "unknown")
    fn_suffix   = tool_input.get("function", "")
    slug = f"{tool.name}_{fn_suffix}" if fn_suffix else tool.name
    slug = re.sub(r"[^\w\-]", "_", slug)

    artifact = analysis_dir(binary_path) / f"{slug}.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(result, encoding="utf-8")

    n_chars = len(result)
    n_tokens_est = n_chars // 4
    return (
        f"[artifact saved → {artifact}  ({n_chars:,} chars / ~{n_tokens_est:,} tokens)]\n"
        f"Full output written to disk. Use a file read tool to access it. "
        f"Re-running this tool is not necessary."
    )
```

Call `_maybe_page` after `tool.safe_execute()` returns, before returning to the tool loop.

The truncation fallback in `tool_loop.py` (lines 259-266) stays as a safety net for any tool
that bypasses the executor.

---

## Phase B — Analysis Manifest Injection

**Depends on**: Phase A (artifacts must exist before manifests are useful)

At the start of each user turn, scan `_analysis/` for existing artifacts and append a brief
manifest to the system prompt:

```
--- Prior analysis artifacts ---
_analysis/proc/ghidra_decompile.txt     (42,311 chars)
_analysis/proc/r2_functions.txt          (1,204 chars)
Use file read tools to access these. Do not re-run the heavy tools.
```

**File**: whichever stage builds the system prompt for each turn (likely `src/runtime/stages/execution.py`
or the direct execution stage). Walk `_analysis/` with `rglob("*.txt") + rglob("*.c")`, list them
with sizes, cap the manifest at 20 lines.

---

## Phase C — Conversational Workflow Convention  *(zero code)*

The staged analysis flow the agent and user follow:

| Turn | User says | Agent does | Artifact written |
|------|-----------|------------|-----------------|
| 1 | "analyze proc" | runs ghidra_decompile | `_analysis/proc/ghidra_decompile.txt` |
| 2 | "generate pseudocode" | reads artifact, writes simplified C | `_analysis/proc/pseudocode.c` |
| 3 | user reviews, annotates | conversation; user edits file directly | — |
| 4 | "write a reconstruction" | reads pseudocode artifact, writes attempt | `_analysis/proc/reconstruction_v1.c` |
| 5 | "compare vs oracle" | diffs behavior | `_analysis/proc/oracle_diff_v1.txt` |

Document this convention in the `deep_disassembly` skill description and the reversing toolset
system prompt so the agent follows it without needing to be told each session.

---

## Implementation Order

1. **Phase D** — session directory consolidation. Foundational; paths must be right before
   anything else writes files.
2. **Phase A** — artifact paging. Kills the 429 and compaction problem.
3. **Phase B** — manifest injection. Quality of life, low risk.
4. **Phase C** — prompt/skill documentation. Zero code.

---

## What this does NOT change

- Tool `execute()` signatures.
- Artifact store (0042) — different concept; this is raw tool output paging.
- `_tests/` or `_plans/` conventions.
- `_logs/`, `_metrics/`, `_events/` are left in place after the migration (manual cleanup).
