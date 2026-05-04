"""ArtifactStore — session-scoped artifact registry backed by SQLite.

Composed from focused mixin classes:
  _SessionMixin      — session lifecycle, conversation persistence
  _CRUDMixin         — named artifact CRUD (set/get/meta/list/expel/pin/flush)
  _DecayMixin        — decay scoring, artifact tagging
  _RecallMixin       — embedding-based semantic recall
  _DiscoveryMixin    — request logging, workflow candidate discovery
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from logger import get_logger
from runtime.artifact_store.schema_sql import (
    INLINE_THRESHOLD,
    _CREATE_ARTIFACTS, _CREATE_ARTIFACT_SESSIONS, _CREATE_SESSIONS,
    _CREATE_CONVERSATION_HISTORY, _CREATE_REQUESTS, _CREATE_WORKFLOW_CANDIDATES,
    _CREATE_ARTIFACT_TAGS, _CREATE_SESSION_SUMMARIES, _CREATE_INDEXES,
)
from runtime.artifact_store.types import ArtifactMeta
from runtime.artifact_store.session import _SessionMixin
from runtime.artifact_store.crud import _CRUDMixin
from runtime.artifact_store.decay import _DecayMixin
from runtime.artifact_store.recall import _RecallMixin
from runtime.artifact_store.discovery import _DiscoveryMixin

logger = get_logger(__name__)


class ArtifactStore(
    _SessionMixin,
    _CRUDMixin,
    _DecayMixin,
    _RecallMixin,
    _DiscoveryMixin,
):
    """Named artifact registry backed by SQLite."""

    def __init__(
        self,
        db_path: Path,
        data_dir: Path,
        inline_threshold: int = INLINE_THRESHOLD,
    ) -> None:
        self._db_path = db_path
        self._data_dir = data_dir
        self._inline_threshold = max(0, int(inline_threshold))
        self._session_id: str = ""
        self._cache: dict[str, Any] = {}
        self._meta: dict[str, ArtifactMeta] = {}
        self._dirty: set[str] = set()
        self._active_project: str | None = None
        self._sqlite_vec_available = False

        db_path.parent.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._init_vector_backend()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(_CREATE_ARTIFACTS)
        cur.execute(_CREATE_ARTIFACT_SESSIONS)
        cur.execute(_CREATE_SESSIONS)
        cur.execute(_CREATE_CONVERSATION_HISTORY)
        cur.execute(_CREATE_REQUESTS)
        cur.execute(_CREATE_WORKFLOW_CANDIDATES)
        cur.execute(_CREATE_ARTIFACT_TAGS)
        cur.execute(_CREATE_SESSION_SUMMARIES)
        # Tier 3 migration: add embedding column to artifacts lazily.
        self._ensure_artifact_summary_embedding_column(cur)
        for sql in _CREATE_INDEXES:
            cur.execute(sql)
        self._conn.commit()

    def _ensure_artifact_summary_embedding_column(self, cur: sqlite3.Cursor) -> None:
        cols = cur.execute("PRAGMA table_info(artifacts)").fetchall()
        names = {row[1] for row in cols}
        if "summary_embedding" not in names:
            cur.execute("ALTER TABLE artifacts ADD COLUMN summary_embedding BLOB")

    def _init_vector_backend(self) -> None:
        self._sqlite_vec_available = False
        try:
            from app_config import config

            vec_cfg = config.artifact_store.sqlite_vec
            if not vec_cfg.enabled:
                return
            ext_path = vec_cfg.extension_path
        except Exception:
            return

        if not ext_path:
            logger.info("artifact_store: sqlite-vec extension path not set; using python cosine fallback")
            return

        try:
            self._conn.enable_load_extension(True)
            self._conn.load_extension(ext_path)
            self._sqlite_vec_available = True
            logger.info(f"artifact_store: sqlite-vec loaded from {ext_path}")
        except Exception as e:
            logger.warning(f"artifact_store: sqlite-vec unavailable ({e}); using python cosine fallback")
        finally:
            try:
                self._conn.enable_load_extension(False)
            except Exception:
                pass


    @property
    def session_id(self) -> str:
        return self._session_id

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: ArtifactStore | None = None


def get_artifact_store() -> ArtifactStore:
    if _store is None:
        raise RuntimeError("ArtifactStore not initialized — call init_store() first")
    return _store


def init_store(
    db_path: Path,
    data_dir: Path,
    inline_threshold: int = INLINE_THRESHOLD,
) -> ArtifactStore:
    global _store
    _store = ArtifactStore(db_path, data_dir, inline_threshold=inline_threshold)
    return _store
