# 07 — Storage and paths

All on-disk state lives under `ARC_HOME` (default `~/.arc/`, overridable
via `ARC_HOME=/path` in `.env`). The project directory stays clean —
no `_sessions/`, `_rag/`, `_analysis/` polluting `git status`.

## Layout

```
$ARC_HOME/
├── sessions/<session_id>/
│   ├── logs/session.log              human-readable timestamps, scope-tagged
│   ├── logs/stderr.log               captured subprocess stderr
│   ├── metrics/council.jsonl         per-deliberation council data
│   ├── events/runtime.jsonl          schema v2 structured events
│   ├── events/blobs/<event_id>.json  paged content (>4 KB)
│   └── session.summary.json          one-shot aggregate at session end
│
├── logs/jvm.log                      Ghidra subprocess output
│
├── rag/
│   ├── global/                       Tier 1 LanceDB warehouse (cross-session)
│   └── sessions/<session_id>/        Tier 2 per-session chunk store
│
├── store/
│   ├── artifacts.db                  SQLite artifact registry (cross-session memory)
│   └── data/                         payload blobs > inline threshold
│
├── ghidra/projects/<binary>_ghidra/  cached Ghidra projects (subprocess-shared)
│
├── analysis/<binary>/                paged tool outputs (heavy decompile etc.)
│
├── plugins/
│   ├── tools/                        filesystem tool plugins (single-file or dirs)
│   └── skills/                       filesystem skill plugins
│
├── agent.db                          SQLModel database (sessions, plans, steps)
├── history                           prompt_toolkit input history
└── settings.yml                      user TUI preferences
```

## Path resolution

`src/session_paths.py` is the source of truth:

- `arc_home()` — resolves once per call from settings (handles `~`,
  creates if missing). Default `~/.arc`, override via
  `ARC_HOME=/path` in `.env`.
- `session_dir(sid)`, `log_path(sid)`, `events_dir(sid)`, … — one
  function per sub-path so call sites never compute paths from `__file__`.
- `ghidra_projects_dir()`, `store_db_path()`, `store_data_dir()` —
  long-lived shared resources.
- `build_analysis_manifest(max_entries, max_chars)` — system-prompt
  embed for the agent (0090b caps).

## Virtual path resolution

The agent uses logical paths in tool calls. `_analysis/<binary>/<file>`
in agent-land = `~/.arc/analysis/<binary>/<file>` on disk. The
translation happens at the tool I/O boundary in
`runtime/path_resolver.py`. This isolation lets the agent reason about
paths consistently regardless of where ARC_HOME points.

## Why centralized

- **Clean repo.** No runtime cruft mixed with source.
- **One backup target.** Point your backup at `~/.arc/`, done.
- **Multi-user friendly.** Each user has their own ARC_HOME by default.
- **Containerizable.** Mount one volume, the whole agent state goes
  with it.

## Bootstrap

`arc bootstrap` is idempotent:

- Creates the directory tree (`sessions/`, `rag/global/`,
  `rag/sessions/`, `store/data/`, `ghidra/projects/`, `analysis/`).
- Runs `alembic upgrade head` against `agent.db` so the SQLModel
  schema is in place.

Run after install, after moving ARC_HOME, or any time the layout
looks wrong. `arc bootstrap --migrate` also walks the legacy
project-dir layout (`_sessions/`, `_rag/`, etc.) and moves anything
found into ARC_HOME.

## Cleanup

```
arc wipe --all           # nuke everything under ~/.arc/
arc wipe --sessions      # just per-session data
arc wipe --rag           # just RAG vectors
arc wipe --analysis      # just paged tool outputs
arc wipe --store         # just artifact store
arc wipe --legacy        # legacy project-dir data
arc wipe --yes           # skip confirmation
```

## Related plans

- `_plans/0085-file-length-audit.md` — original ARC_HOME centralization.
- `_plans/0086-runtime-drift-cleanup.md` — path/import cleanup.
- `_plans/0087-telemetry-overhaul.md` — events/blobs layout.
- `_plans/0090b-implementation.md` — analysis manifest size cap.
