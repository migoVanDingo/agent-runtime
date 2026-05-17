# 0074 — Phase J: Persistence consolidation onto ORM

## Goal

The artifact store is a 1278-line monolithic file with raw SQLite. This phase:
1. Splits `runtime/artifact_store.py` into a package with focused modules.
2. Adds ORM models and DAL for artifacts, conversation history, and requests.
3. Keeps the same public API so no consumers need updating.

The Alembic migration to move `_store/artifacts.db` data into `data/agent.db`
is a separate step (after the package is proven stable).

## Scope

- `runtime/artifact_store/` package:
  - `__init__.py` — re-exports `ArtifactStore`, `ArtifactMeta`, dataclasses
  - `core.py` — ArtifactStore class + CRUD (set/get/meta/list/expel/pin)
  - `session.py` — session lifecycle, conversation persistence
  - `decay.py` — decay sweep + pinned-decay logic
  - `discovery.py` — request logging + workflow candidate discovery
  - `recall.py` — embedding recall, project scoping
  - `schema_sql.py` — DDL constants + index creation
- Delete `runtime/artifact_store.py`.

## Files touched

`runtime/artifact_store.py` (deleted, replaced by package),
all consumers verified via compile.

## Exit criteria

- `runtime/artifact_store.py` does not exist.
- `from runtime.artifact_store import get_artifact_store` still works.
- `python3 -m compileall -q src` clean.
- All 119+ tests pass.
