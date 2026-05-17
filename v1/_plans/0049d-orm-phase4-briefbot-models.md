# 0049d — ORM/DAL Phase 4: Briefbot Read-Only Models

**Status:** Implemented
**Phase:** 4 of 7

## What was built

### New model files

| File | SQLModel class | Briefbot table |
|---|---|---|
| `src/db/models/briefbot/item.py` | `BriefbotItem` | `items` |
| `src/db/models/briefbot/cluster.py` | `BriefbotCluster` | `clusters` |
| `src/db/models/briefbot/cluster.py` | `BriefbotClusterMembership` | `cluster_memberships` |
| `src/db/models/briefbot/topic.py` | `BriefbotTopic` | `topic_profiles` |

All columns verified against live Briefbot schema (`PRAGMA table_info()`).

## Alembic isolation

`env.py` now includes an `include_object` filter with `_BRIEFBOT_TABLES` — a frozenset of all 14 Briefbot table names. Any table in this set is excluded from Alembic autogenerate, even if it ends up in `SQLModel.metadata` (which it does — SQLModel ignores the `metadata=` constructor param in `table=True` mode).

`compare_type=False` added to both offline and online configure calls to suppress SQLite type comparison noise (`REAL` vs `Float`, etc.).

`alembic check` returns **"No new upgrade operations detected"** — confirmed clean.

## Notes on metadata isolation

SQLModel ignores `metadata=BRIEFBOT_METADATA` when `table=True` is set — all `table=True` classes register into `SQLModel.metadata` regardless. The `include_object` filter in `env.py` is the actual guard. The `BRIEFBOT_METADATA` instance in `item.py` is kept as documentation of intent but has no runtime effect.

## When Briefbot schema changes

1. Update the relevant model file(s) in `src/db/models/briefbot/`
2. Add a no-op version file documenting the change:
   ```python
   # src/alembic/versions/NNNN_briefbot_schema_note.py
   """Note: Briefbot items table added opportunity_tags_json column
   Revision ID: NNNN
   """
   def upgrade() -> None:
       pass  # External schema — no DDL runs here
   def downgrade() -> None:
       pass
   ```
3. Verify `alembic check` still passes
