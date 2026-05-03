"""Session-scoped artifact store backed by SQLite.

Tier 1:
- Named artifact CRUD with inline/file-backed storage.

Tier 2:
- Session resumption + detached-session listing.
- Conversation history persistence.
- Decay scoring + archive tags.
- Request logging + workflow candidate discovery.

Tier 3:
- Session and artifact semantic recall.
- Project scoping for memory.
- Pinned boost with slower decay/archive.
"""
from __future__ import annotations

import json
import sqlite3
import time
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from logger import get_logger

logger = get_logger(__name__)

# Inline storage threshold — values larger than this go to disk
INLINE_THRESHOLD = 4096  # bytes

_KIND_EXTENSIONS: dict[str, str] = {
    "url_content": "txt",
    "result": "txt",
    "string": "txt",
    "file": "txt",
    "path": "txt",
}

_CREATE_ARTIFACTS = """\
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
)"""

_CREATE_ARTIFACT_SESSIONS = """\
CREATE TABLE IF NOT EXISTS artifact_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    key         TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    accessed_at REAL    NOT NULL
)"""

_CREATE_SESSIONS = """\
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT    PRIMARY KEY,
    started_at     REAL    NOT NULL,
    ended_at       REAL,
    artifact_count INTEGER DEFAULT 0,
    resumable      INTEGER DEFAULT 1
)"""

_CREATE_CONVERSATION_HISTORY = """\
CREATE TABLE IF NOT EXISTS conversation_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    turn       INTEGER NOT NULL,
    created_at REAL    NOT NULL
)"""

_CREATE_REQUESTS = """\
CREATE TABLE IF NOT EXISTS requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    message    TEXT    NOT NULL,
    embedding  BLOB,
    workflow   TEXT,
    created_at REAL    NOT NULL
)"""

_CREATE_WORKFLOW_CANDIDATES = """\
CREATE TABLE IF NOT EXISTS workflow_candidates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT,
    description   TEXT    NOT NULL,
    example_ids   TEXT    NOT NULL,
    frequency     INTEGER NOT NULL,
    last_seen     REAL    NOT NULL,
    recency_score REAL    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'candidate',
    approved_at   REAL
)"""

_CREATE_ARTIFACT_TAGS = """\
CREATE TABLE IF NOT EXISTS artifact_tags (
    key   TEXT NOT NULL,
    tag   TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (key, tag)
)"""

_CREATE_SESSION_SUMMARIES = """\
CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT PRIMARY KEY,
    summary    TEXT NOT NULL,
    embedding  BLOB NOT NULL,
    created_at REAL NOT NULL
)"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_artifacts_session_id ON artifacts(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_artifact_sessions_session_id ON artifact_sessions(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_conversation_history_session_turn ON conversation_history(session_id, turn)",
    "CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_requests_session_id ON requests(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_workflow_candidates_status_last_seen ON workflow_candidates(status, last_seen)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_resumable_ended ON sessions(resumable, ended_at, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_session_summaries_created_at ON session_summaries(created_at)",
]


@dataclass
class ArtifactMeta:
    key: str
    kind: str
    summary: str
    source: str
    session_id: str
    created_at: float
    last_accessed: float
    access_count: int
    decay_score: float
    permanent: bool
    has_value: bool
    has_data_path: bool
    data_path: str | None = None


@dataclass
class ResumableSession:
    session_id: str
    started_at: float
    artifact_count: int
    preview: str


@dataclass
class WorkflowCandidate:
    id: int
    description: str
    example_ids: list[int]
    frequency: int
    last_seen: float
    recency_score: float
    status: str
    approved_at: float | None
    example_messages: list[str]


@dataclass
class SessionRecall:
    session_id: str
    summary: str
    score: float
    created_at: float


@dataclass
class ArtifactRecall:
    key: str
    kind: str
    summary: str
    source: str
    session_id: str
    score: float
    project: str | None = None


@dataclass
class _RequestRow:
    id: int
    message: str
    embedding: list[float]
    created_at: float


def _summary_for_df(df: Any) -> str:
    try:
        cols = ", ".join(f"{c} ({df[c].dtype})" for c in df.columns)
        sample = df.head(3).to_string(index=False)
        return f"shape={df.shape}  columns=[{cols}]\n{sample}"
    except Exception:
        return f"dataframe shape={getattr(df, 'shape', '?')}"


def _summary_for_text(text: str) -> str:
    return f"{len(text):,} chars\n{text[:300]}"


def _serialize(value: Any, kind: str) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _deserialize(raw: str, kind: str) -> Any:
    if kind in ("url_content", "result", "string", "file", "path"):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def _vec_to_blob(vec: list[float]) -> bytes:
    a = array("f", vec)
    return a.tobytes()


def _blob_to_vec(blob: bytes) -> list[float]:
    a = array("f")
    a.frombytes(blob)
    return list(a)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


class ArtifactStore:
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

    # ── Session lifecycle ───────────────────────────────────────────────

    def init_session(self, session_id: str) -> None:
        """Register new session and set active session ID."""
        self._session_id = session_id
        now = time.time()
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, started_at, ended_at, resumable, artifact_count) "
            "VALUES (?, ?, NULL, 1, COALESCE((SELECT artifact_count FROM sessions WHERE session_id = ?), 0))",
            (session_id, now, session_id),
        )
        self._conn.commit()
        logger.debug(f"ArtifactStore: session {session_id} initialized")

    def load_session(self, resume_id: str) -> str:
        """Load existing resumable session as active session."""
        row = self._conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = ? AND resumable = 1 AND ended_at IS NULL",
            (resume_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Session '{resume_id}' is not resumable or does not exist")

        self._session_id = row["session_id"]
        self._cache.clear()
        self._meta.clear()
        self._dirty.clear()
        self.load_artifact_meta_for_session(self._session_id)
        logger.info(f"ArtifactStore: resumed session {self._session_id}")
        return self._session_id

    def mark_detached(self, session_id: str | None = None) -> None:
        sid = session_id or self._session_id
        if not sid:
            return
        self._conn.execute(
            "UPDATE sessions SET ended_at = NULL, resumable = 1 WHERE session_id = ?",
            (sid,),
        )
        self._conn.commit()

    def mark_closed(self, session_id: str | None = None) -> None:
        sid = session_id or self._session_id
        if not sid:
            return
        now = time.time()
        self._conn.execute(
            "UPDATE sessions SET ended_at = ?, resumable = 0 WHERE session_id = ?",
            (now, sid),
        )
        self._conn.commit()

    def set_active_project(self, project: str | None) -> None:
        p = (project or "").strip()
        self._active_project = p or None

    def get_active_project(self) -> str | None:
        return self._active_project

    def list_resumable_sessions(self, limit: int = 20) -> list[ResumableSession]:
        rows = self._conn.execute(
            "SELECT session_id, started_at, COALESCE(artifact_count, 0) AS artifact_count "
            "FROM sessions WHERE resumable = 1 AND ended_at IS NULL "
            "ORDER BY started_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()

        result: list[ResumableSession] = []
        for row in rows:
            sid = row["session_id"]
            preview = self._first_user_message_preview(sid) or "(no preview available)"
            result.append(
                ResumableSession(
                    session_id=sid,
                    started_at=float(row["started_at"]),
                    artifact_count=int(row["artifact_count"]),
                    preview=preview,
                )
            )
        return result

    def _first_user_message_preview(self, session_id: str) -> str | None:
        rows = self._conn.execute(
            "SELECT content FROM conversation_history "
            "WHERE session_id = ? AND role = 'user' ORDER BY turn ASC",
            (session_id,),
        ).fetchall()
        for row in rows:
            try:
                parsed = json.loads(row["content"])
            except Exception:
                continue
            if isinstance(parsed, str):
                text = parsed.strip().replace("\n", " ")
                if text:
                    return text[:160]
        return None

    # ── Conversation persistence ───────────────────────────────────────

    def save_conversation(self, messages: list[dict]) -> int:
        if not self._session_id:
            return 0
        now = time.time()
        cur = self._conn.cursor()
        cur.execute("DELETE FROM conversation_history WHERE session_id = ?", (self._session_id,))
        inserted = 0
        for i, msg in enumerate(messages):
            role = str(msg.get("role", ""))
            content = json.dumps(msg.get("content"), ensure_ascii=False)
            cur.execute(
                "INSERT INTO conversation_history (session_id, role, content, turn, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (self._session_id, role, content, i, now),
            )
            inserted += 1
        self._conn.commit()
        return inserted

    def load_conversation(self, session_id: str | None = None) -> list[dict]:
        sid = session_id or self._session_id
        if not sid:
            return []

        rows = self._conn.execute(
            "SELECT role, content FROM conversation_history WHERE session_id = ? ORDER BY turn ASC",
            (sid,),
        ).fetchall()
        messages: list[dict] = []
        for row in rows:
            try:
                content = json.loads(row["content"])
            except Exception:
                logger.warning("ArtifactStore.load_conversation: skipping malformed row")
                continue
            messages.append({"role": row["role"], "content": content})
        return messages

    # ── Core artifact CRUD ─────────────────────────────────────────────

    def flush(self) -> None:
        """Persist all dirty artifacts to SQLite."""
        if not self._session_id:
            return

        cur = self._conn.cursor()
        now = time.time()
        dirty_keys = list(self._dirty)

        for key in dirty_keys:
            m = self._meta.get(key)
            if m is None:
                continue
            value_serialized: str | None = None
            if m.has_value:
                raw = self._cache.get(key)
                if raw is not None:
                    value_serialized = _serialize(raw, m.kind)

            cur.execute(
                """
                INSERT OR REPLACE INTO artifacts
                    (key, kind, value, summary, source, data_path,
                     session_id, created_at, last_accessed, access_count,
                     decay_score, permanent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    m.key,
                    m.kind,
                    value_serialized,
                    m.summary,
                    m.source,
                    m.data_path,
                    m.session_id,
                    m.created_at,
                    m.last_accessed,
                    m.access_count,
                    m.decay_score,
                    int(m.permanent),
                ),
            )

        for key in dirty_keys:
            cur.execute(
                "INSERT INTO artifact_sessions (session_id, key, action, accessed_at) VALUES (?, ?, ?, ?)",
                (self._session_id, key, "set", now),
            )
            m = self._meta.get(key)
            if m and m.summary:
                emb = self._embed_text(m.summary)
                if emb is not None:
                    cur.execute(
                        "UPDATE artifacts SET summary_embedding = ? WHERE key = ?",
                        (_vec_to_blob(emb), key),
                    )

        cur.execute(
            "UPDATE sessions SET artifact_count = ?, ended_at = NULL, resumable = 1 WHERE session_id = ?",
            (len(self.list()), self._session_id),
        )

        self._conn.commit()
        self._dirty.clear()
        logger.info(
            f"ArtifactStore: flushed {len(dirty_keys)} dirty artifact(s) "
            f"for session {self._session_id}"
        )

    def set(self, key: str, value: Any, kind: str, source: str = "") -> ArtifactMeta:
        now = time.time()
        data_path: str | None = None
        has_value = False
        has_data_path = False
        summary = ""
        existing = self._meta.get(key) or self.meta(key)

        if kind == "dataframe":
            parquet_path = self._data_dir / f"{key}.parquet"
            value.to_parquet(str(parquet_path), index=False)
            data_path = str(parquet_path)
            has_data_path = True
            summary = _summary_for_df(value)
        elif kind == "url_content":
            serialized = _serialize(value, kind)
            file_path = self._data_dir / f"{key}.txt"
            file_path.write_text(serialized, encoding="utf-8")
            data_path = str(file_path)
            has_data_path = True
            summary = _summary_for_text(serialized)
        else:
            serialized = _serialize(value, kind)
            if len(serialized.encode()) <= self._inline_threshold:
                has_value = True
                summary = _summary_for_text(serialized)
            else:
                ext = _KIND_EXTENSIONS.get(kind, "json")
                file_path = self._data_dir / f"{key}.{ext}"
                file_path.write_text(serialized, encoding="utf-8")
                data_path = str(file_path)
                has_data_path = True
                summary = _summary_for_text(serialized)

        if existing and existing.data_path and existing.data_path != data_path:
            try:
                Path(existing.data_path).unlink(missing_ok=True)
            except Exception:
                pass

        created_at = existing.created_at if existing else now

        meta = ArtifactMeta(
            key=key,
            kind=kind,
            summary=summary,
            source=source,
            session_id=self._session_id,
            created_at=created_at,
            last_accessed=now,
            access_count=(existing.access_count if existing else 0),
            decay_score=(existing.decay_score if existing else 1.0),
            permanent=(existing.permanent if existing else False),
            has_value=has_value,
            has_data_path=has_data_path,
            data_path=data_path,
        )

        self._meta[key] = meta
        self._cache[key] = value
        self._dirty.add(key)
        if self._active_project:
            self.set_tag(key, "project", self._active_project)

        if self._session_id:
            try:
                from runtime.persistence import PersistenceWriter
                size = len(meta.summary.encode()) if meta.summary else None
                PersistenceWriter.record_artifact(
                    db_session_id=self._session_id,
                    key=key,
                    tier="hot",
                    size_bytes=size,
                    content_preview=meta.summary[:500] if meta.summary else None,
                    storage_path=data_path,
                )
            except Exception:
                pass

        return meta

    def get(self, key: str) -> Any | None:
        if key in self._cache:
            m = self._meta.get(key) or self.meta(key)
            if m:
                m.last_accessed = time.time()
                m.access_count += 1
                self._dirty.add(key)
            return self._cache[key]

        row = self._conn.execute(
            "SELECT kind, value, data_path FROM artifacts WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None

        kind = row["kind"]
        if row["value"] is not None:
            value = _deserialize(row["value"], kind)
        elif row["data_path"] is not None:
            dp = Path(row["data_path"])
            if not dp.exists():
                logger.warning(f"ArtifactStore.get: data_path missing for key={key!r}")
                return None
            if kind == "dataframe":
                import pandas as pd

                value = pd.read_parquet(str(dp))
            else:
                value = _deserialize(dp.read_text(encoding="utf-8"), kind)
        else:
            return None

        now = time.time()
        self._cache[key] = value
        m = self._meta.get(key) or self.meta(key)
        if m:
            m.last_accessed = now
            m.access_count += 1
        self._conn.execute(
            "UPDATE artifacts SET last_accessed = ?, access_count = access_count + 1 WHERE key = ?",
            (now, key),
        )
        self._conn.commit()
        return value

    def meta(self, key: str) -> ArtifactMeta | None:
        if key in self._meta:
            return self._meta[key]

        row = self._conn.execute(
            """SELECT key, kind, summary, source, session_id, created_at,
                      last_accessed, access_count, decay_score, permanent,
                      value, data_path
               FROM artifacts WHERE key = ?""",
            (key,),
        ).fetchone()
        if row is None:
            return None

        m = ArtifactMeta(
            key=row["key"],
            kind=row["kind"],
            summary=row["summary"] or "",
            source=row["source"] or "",
            session_id=row["session_id"],
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            access_count=row["access_count"],
            decay_score=row["decay_score"],
            permanent=bool(row["permanent"]),
            has_value=row["value"] is not None,
            has_data_path=row["data_path"] is not None,
            data_path=row["data_path"],
        )
        self._meta[key] = m
        return m

    def list(self, kind: str | None = None) -> list[ArtifactMeta]:
        rows = self._conn.execute(
            "SELECT key FROM artifacts" + (" WHERE kind = ?" if kind else ""),
            (kind,) if kind else (),
        ).fetchall()

        db_keys = {r["key"] for r in rows}
        all_keys = db_keys | set(self._meta.keys())

        result = []
        for k in all_keys:
            m = self._meta.get(k) or self.meta(k)
            if m and (kind is None or m.kind == kind):
                result.append(m)

        result.sort(key=lambda m: m.created_at)
        return result

    def expel(self, key: str) -> bool:
        found = key in self._meta or self._conn.execute(
            "SELECT 1 FROM artifacts WHERE key = ?", (key,)
        ).fetchone() is not None
        if not found:
            return False

        m = self._meta.get(key) or self.meta(key)
        if m and m.data_path:
            dp = Path(m.data_path)
            if dp.exists():
                dp.unlink()

        self._cache.pop(key, None)
        self._meta.pop(key, None)
        self._dirty.discard(key)

        now = time.time()
        self._conn.execute(
            "INSERT INTO artifact_sessions (session_id, key, action, accessed_at) VALUES (?, ?, ?, ?)",
            (self._session_id, key, "expel", now),
        )
        self._conn.execute("DELETE FROM artifacts WHERE key = ?", (key,))
        self._conn.execute("DELETE FROM artifact_tags WHERE key = ?", (key,))
        self._conn.commit()
        return True

    def expel_pattern(self, pattern: str) -> list[str]:
        import fnmatch

        all_keys = [m.key for m in self.list()]
        matched = [k for k in all_keys if fnmatch.fnmatch(k, pattern)]
        for k in matched:
            self.expel(k)
        return matched

    def pin(self, key: str) -> None:
        m = self._meta.get(key) or self.meta(key)
        if m:
            m.permanent = True
            self._dirty.add(key)
            self._conn.execute("UPDATE artifacts SET permanent = 1 WHERE key = ?", (key,))
            self._conn.commit()

    # ── Tier 2: decay + tags ───────────────────────────────────────────

    def load_artifact_meta_for_session(self, session_id: str) -> int:
        rows = self._conn.execute(
            """SELECT key, kind, summary, source, session_id, created_at,
                      last_accessed, access_count, decay_score, permanent,
                      value, data_path
               FROM artifacts WHERE session_id = ?""",
            (session_id,),
        ).fetchall()
        count = 0
        for row in rows:
            self._meta[row["key"]] = ArtifactMeta(
                key=row["key"],
                kind=row["kind"],
                summary=row["summary"] or "",
                source=row["source"] or "",
                session_id=row["session_id"],
                created_at=row["created_at"],
                last_accessed=row["last_accessed"],
                access_count=row["access_count"],
                decay_score=row["decay_score"],
                permanent=bool(row["permanent"]),
                has_value=row["value"] is not None,
                has_data_path=row["data_path"] is not None,
                data_path=row["data_path"],
            )
            count += 1
        return count

    def set_tag(self, key: str, tag: str, value: str = "1") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO artifact_tags (key, tag, value) VALUES (?, ?, ?)",
            (key, tag, value),
        )
        self._conn.commit()

    def get_tag(self, key: str, tag: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM artifact_tags WHERE key = ? AND tag = ?",
            (key, tag),
        ).fetchone()
        if row is None:
            return None
        return row["value"]

    def apply_decay(self, factor: float = 0.85, threshold: float = 0.1) -> list[str]:
        if factor <= 0:
            return []
        archived: list[str] = []
        rows = self._conn.execute(
            "SELECT key, decay_score, permanent FROM artifacts WHERE session_id != ?",
            (self._session_id,),
        ).fetchall()
        for row in rows:
            is_permanent = bool(row["permanent"])
            # Pinned memory decays and archives more slowly than normal artifacts.
            eff_factor = factor
            eff_threshold = threshold
            if is_permanent:
                eff_factor = min(0.995, 1.0 - ((1.0 - factor) * 0.35))
                eff_threshold = threshold * 0.5

            new_score = float(row["decay_score"]) * eff_factor
            self._conn.execute(
                "UPDATE artifacts SET decay_score = ? WHERE key = ?",
                (new_score, row["key"]),
            )
            if new_score < eff_threshold:
                self.set_tag(row["key"], "archived", "1")
                archived.append(row["key"])
        self._conn.commit()
        return archived

    # ── Tier 3: embeddings + recall ───────────────────────────────────

    def _embed_text(self, text: str) -> list[float] | None:
        content = (text or "").strip()
        if not content:
            return None
        try:
            from embeddings import get_embedding_model

            model = get_embedding_model()
            emb = model.encode(content[:4000], show_progress_bar=False)
            if hasattr(emb, "tolist"):
                emb = emb.tolist()
            return [float(x) for x in emb]
        except Exception as e:
            logger.warning(f"ArtifactStore: embedding unavailable ({e})")
            return None

    def index_session_summary(self, session_id: str, summary: str) -> bool:
        sid = (session_id or "").strip()
        s = (summary or "").strip()
        if not sid or not s:
            return False
        emb = self._embed_text(s)
        if emb is None:
            return False
        self._conn.execute(
            "INSERT OR REPLACE INTO session_summaries (session_id, summary, embedding, created_at) "
            "VALUES (?, ?, ?, ?)",
            (sid, s[:1200], _vec_to_blob(emb), time.time()),
        )
        self._conn.commit()
        return True

    def index_artifact_summary(self, key: str) -> bool:
        m = self._meta.get(key) or self.meta(key)
        if m is None or not m.summary:
            return False
        emb = self._embed_text(m.summary)
        if emb is None:
            return False
        self._conn.execute(
            "UPDATE artifacts SET summary_embedding = ? WHERE key = ?",
            (_vec_to_blob(emb), key),
        )
        self._conn.commit()
        return True

    def reindex_all_missing_embeddings(self, limit: int = 500) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT key, summary FROM artifacts "
            "WHERE summary IS NOT NULL AND summary != '' AND summary_embedding IS NULL "
            "LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        indexed = 0
        skipped = 0
        for row in rows:
            key = str(row["key"])
            summary = str(row["summary"] or "")
            emb = self._embed_text(summary)
            if emb is None:
                skipped += 1
                continue
            self._conn.execute(
                "UPDATE artifacts SET summary_embedding = ? WHERE key = ?",
                (_vec_to_blob(emb), key),
            )
            indexed += 1
        self._conn.commit()
        return {"indexed": indexed, "skipped": skipped, "total": len(rows)}

    def _resolve_project_filter(self, project: str | None) -> str | None:
        if project == "*":
            return None
        if project is not None:
            p = project.strip()
            return p or None
        return self._active_project

    def recall_sessions(
        self,
        query: str,
        top_k: int = 3,
        threshold: float = 0.6,
        project: str | None = None,
    ) -> list[SessionRecall]:
        qv = self._embed_text(query)
        if qv is None:
            return []

        project_filter = self._resolve_project_filter(project)
        if project_filter:
            rows = self._conn.execute(
                "SELECT ss.session_id, ss.summary, ss.embedding, ss.created_at "
                "FROM session_summaries ss "
                "WHERE EXISTS ("
                "  SELECT 1 FROM artifacts a "
                "  JOIN artifact_tags t ON t.key = a.key AND t.tag = 'project' "
                "  WHERE a.session_id = ss.session_id AND t.value = ?"
                ")",
                (project_filter,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT session_id, summary, embedding, created_at FROM session_summaries"
            ).fetchall()

        out: list[SessionRecall] = []
        for row in rows:
            blob = row["embedding"]
            if not blob:
                continue
            try:
                score = _cosine_similarity(qv, _blob_to_vec(blob))
            except Exception:
                continue
            if score < threshold:
                continue
            out.append(
                SessionRecall(
                    session_id=str(row["session_id"]),
                    summary=str(row["summary"] or ""),
                    score=float(score),
                    created_at=float(row["created_at"] or 0.0),
                )
            )

        out.sort(key=lambda x: x.score, reverse=True)
        return out[: max(1, int(top_k))]

    def recall_artifacts(
        self,
        query: str,
        top_k: int = 3,
        threshold: float = 0.6,
        project: str | None = None,
    ) -> list[ArtifactRecall]:
        qv = self._embed_text(query)
        if qv is None:
            return []

        project_filter = self._resolve_project_filter(project)
        params: tuple[Any, ...]
        sql = (
            "SELECT a.key, a.kind, a.summary, a.source, a.session_id, a.summary_embedding, "
            "a.permanent, t.value AS project "
            "FROM artifacts a "
            "LEFT JOIN artifact_tags t ON t.key = a.key AND t.tag = 'project' "
            "WHERE a.summary_embedding IS NOT NULL"
        )
        if project_filter:
            sql += " AND t.value = ?"
            params = (project_filter,)
        else:
            params = ()
        rows = self._conn.execute(sql, params).fetchall()

        out: list[ArtifactRecall] = []
        for row in rows:
            blob = row["summary_embedding"]
            if not blob:
                continue
            try:
                score = _cosine_similarity(qv, _blob_to_vec(blob))
            except Exception:
                continue
            if bool(row["permanent"]):
                score = min(1.0, score + 0.05)
            if score < threshold:
                continue
            out.append(
                ArtifactRecall(
                    key=str(row["key"]),
                    kind=str(row["kind"]),
                    summary=str(row["summary"] or ""),
                    source=str(row["source"] or ""),
                    session_id=str(row["session_id"]),
                    score=float(score),
                    project=str(row["project"]) if row["project"] else None,
                )
            )

        out.sort(key=lambda x: x.score, reverse=True)
        return out[: max(1, int(top_k))]

    # ── Tier 2: request logging + workflow discovery ───────────────────

    def record_request(self, message: str, workflow: str | None = None) -> int | None:
        if not self._session_id:
            return None
        now = time.time()

        embedding_blob: bytes | None = None
        try:
            from embeddings import get_embedding_model

            model = get_embedding_model()
            emb = model.encode(message, show_progress_bar=False)
            if hasattr(emb, "tolist"):
                emb = emb.tolist()
            embedding_blob = _vec_to_blob([float(x) for x in emb])
        except Exception as e:
            logger.warning(f"ArtifactStore.record_request: embedding unavailable ({e})")

        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO requests (session_id, message, embedding, workflow, created_at) VALUES (?, ?, ?, ?, ?)",
            (self._session_id, message, embedding_blob, workflow, now),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def discover_workflows(
        self,
        lookback_days: int = 30,
        similarity_threshold: float = 0.82,
        frequency_threshold: int = 5,
        recency_decay: float = 0.95,
    ) -> list[WorkflowCandidate]:
        now = time.time()
        cutoff = now - max(1, int(lookback_days)) * 86400
        rows = self._conn.execute(
            "SELECT id, message, embedding, created_at FROM requests "
            "WHERE created_at >= ? AND embedding IS NOT NULL ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
        reqs: list[_RequestRow] = []
        for r in rows:
            try:
                emb = _blob_to_vec(r["embedding"])
            except Exception:
                continue
            reqs.append(
                _RequestRow(
                    id=int(r["id"]),
                    message=str(r["message"]),
                    embedding=emb,
                    created_at=float(r["created_at"]),
                )
            )

        if len(reqs) < max(2, frequency_threshold):
            return []

        n = len(reqs)
        adj: list[list[int]] = [[] for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                sim = _cosine_similarity(reqs[i].embedding, reqs[j].embedding)
                if sim >= similarity_threshold:
                    adj[i].append(j)
                    adj[j].append(i)

        visited = [False] * n
        clusters: list[list[int]] = []
        for i in range(n):
            if visited[i]:
                continue
            stack = [i]
            visited[i] = True
            comp = []
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for nxt in adj[cur]:
                    if not visited[nxt]:
                        visited[nxt] = True
                        stack.append(nxt)
            if len(comp) >= frequency_threshold:
                clusters.append(comp)

        discovered: list[WorkflowCandidate] = []
        for comp in clusters:
            comp_rows = [reqs[i] for i in comp]
            comp_rows.sort(key=lambda r: r.created_at, reverse=True)
            frequency = len(comp_rows)
            recency_score = 0.0
            for r in comp_rows:
                days_ago = max(0.0, (now - r.created_at) / 86400.0)
                recency_score += recency_decay ** days_ago

            example_ids = [r.id for r in comp_rows[:5]]
            exemplar = comp_rows[0].message.strip().replace("\n", " ")
            if len(exemplar) > 100:
                exemplar = exemplar[:97] + "..."
            description = f"Recurring requests similar to: '{exemplar}'"

            existing = self._conn.execute(
                "SELECT id FROM workflow_candidates WHERE description = ? AND status IN ('candidate', 'approved')",
                (description,),
            ).fetchone()
            if existing is None:
                cur = self._conn.cursor()
                cur.execute(
                    "INSERT INTO workflow_candidates "
                    "(name, description, example_ids, frequency, last_seen, recency_score, status, approved_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'candidate', NULL)",
                    (
                        None,
                        description,
                        json.dumps(example_ids),
                        frequency,
                        comp_rows[0].created_at,
                        recency_score,
                    ),
                )
                cid = int(cur.lastrowid)
                self._conn.commit()
            else:
                cid = int(existing["id"])
                self._conn.execute(
                    "UPDATE workflow_candidates SET example_ids = ?, frequency = ?, last_seen = ?, recency_score = ? "
                    "WHERE id = ?",
                    (
                        json.dumps(example_ids),
                        frequency,
                        comp_rows[0].created_at,
                        recency_score,
                        cid,
                    ),
                )
                self._conn.commit()

            c = self.get_workflow_candidate(cid)
            if c is not None:
                discovered.append(c)

        return discovered

    def get_workflow_candidate(self, candidate_id: int) -> WorkflowCandidate | None:
        row = self._conn.execute(
            "SELECT id, description, example_ids, frequency, last_seen, recency_score, status, approved_at "
            "FROM workflow_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        ids = _safe_int_list(row["example_ids"])
        messages = self._messages_for_request_ids(ids)
        return WorkflowCandidate(
            id=int(row["id"]),
            description=str(row["description"]),
            example_ids=ids,
            frequency=int(row["frequency"]),
            last_seen=float(row["last_seen"]),
            recency_score=float(row["recency_score"]),
            status=str(row["status"]),
            approved_at=float(row["approved_at"]) if row["approved_at"] is not None else None,
            example_messages=messages,
        )

    def get_pending_workflow_candidates(self, limit: int = 10) -> list[WorkflowCandidate]:
        rows = self._conn.execute(
            "SELECT id FROM workflow_candidates WHERE status = 'candidate' ORDER BY recency_score DESC, last_seen DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        out: list[WorkflowCandidate] = []
        for row in rows:
            c = self.get_workflow_candidate(int(row["id"]))
            if c is not None:
                out.append(c)
        return out

    def approve_workflow_candidate(self, candidate_id: int) -> None:
        self._conn.execute(
            "UPDATE workflow_candidates SET status = 'approved', approved_at = ? WHERE id = ?",
            (time.time(), candidate_id),
        )
        self._conn.commit()

    def reject_workflow_candidate(self, candidate_id: int) -> None:
        self._conn.execute(
            "UPDATE workflow_candidates SET status = 'rejected' WHERE id = ?",
            (candidate_id,),
        )
        self._conn.commit()

    def _messages_for_request_ids(self, ids: list[int]) -> list[str]:
        if not ids:
            return []
        out: list[str] = []
        for rid in ids:
            row = self._conn.execute("SELECT message FROM requests WHERE id = ?", (rid,)).fetchone()
            if row is None:
                continue
            msg = str(row["message"]).strip().replace("\n", " ")
            if len(msg) > 120:
                msg = msg[:117] + "..."
            out.append(msg)
        return out

    # ── Utilities ───────────────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: ArtifactStore | None = None


def _safe_int_list(raw: str) -> list[int]:
    try:
        v = json.loads(raw)
    except Exception:
        return []
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        try:
            out.append(int(x))
        except Exception:
            continue
    return out


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
