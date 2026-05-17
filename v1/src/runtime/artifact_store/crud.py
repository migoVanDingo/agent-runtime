"""Core artifact CRUD operations for ArtifactStore."""
from __future__ import annotations
import time
from pathlib import Path
from typing import Any
from runtime.artifact_store.types import ArtifactMeta
from runtime.artifact_store.schema_sql import _serialize, _deserialize, _summary_for_df, _summary_for_text, _KIND_EXTENSIONS
from logger import get_logger
logger = get_logger(__name__)


def _emit_artifact_event(event_type: str, *, payload: dict) -> None:
    """Best-effort artifact telemetry — never raise."""
    try:
        from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
        get_event_bus().emit(RuntimeEvent(
            event_type,
            get_runtime_identity(),
            payload=payload,
            stage="ArtifactStore",
        ))
    except Exception:
        pass


class _CRUDMixin:

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

        _emit_artifact_event(
            "artifact.stored",
            payload={
                "key": key,
                "kind": kind,
                "summary_preview": (meta.summary or "")[:200],
                "stored_inline": has_value,
                "has_data_path": has_data_path,
            },
        )
        return meta

    def get(self, key: str) -> Any | None:
        if key in self._cache:
            m = self._meta.get(key) or self.meta(key)
            if m:
                m.last_accessed = time.time()
                m.access_count += 1
                self._dirty.add(key)
            _emit_artifact_event("artifact.read", payload={"key": key, "cache_hit": True})
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
        _emit_artifact_event("artifact.read", payload={"key": key, "cache_hit": False})
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
        _emit_artifact_event("artifact.expelled", payload={"key": key})
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

