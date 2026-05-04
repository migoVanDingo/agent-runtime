"""Embedding-based semantic recall for ArtifactStore."""
from __future__ import annotations
import time
from typing import Any
from runtime.artifact_store.types import ArtifactMeta, SessionRecall, ArtifactRecall
from runtime.artifact_store.schema_sql import _vec_to_blob, _blob_to_vec, _cosine_similarity
from logger import get_logger
logger = get_logger(__name__)

class _RecallMixin:

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

