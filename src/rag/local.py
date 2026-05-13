"""LocalRagService — LanceDB-backed in-process implementation of RagService.

Storage layout:
  Tier 1 (global warehouse): <rag_global_uri>/  — one row per session
  Tier 2 (session chunks):   <rag_session_uri(id)>/  — one table per session

Both URIs resolve to local paths in dev and gs:// URIs in production.
LanceDB handles both transparently.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from logger import get_logger
from rag.schema import Chunk, SessionHit, ChunkHit
from rag.service import RagService

if TYPE_CHECKING:
    from rag.embedder import Embedder

logger = get_logger(__name__)


class LocalRagService(RagService):
    def __init__(self, embedder: "Embedder", global_uri: str, cfg: Any) -> None:
        """
        Args:
            embedder: provider-specific embedding model
            global_uri: LanceDB URI for the Tier 1 global warehouse
            cfg: RagConfig instance (top_k, threshold, injection_budget_chars)
        """
        import lancedb
        self._embedder = embedder
        self._cfg = cfg
        self._global_db = lancedb.connect(global_uri)
        self._sessions_tbl: Any = None
        self._chunk_dbs: dict[str, Any] = {}
        self._chunk_tbls: dict[str, Any] = {}

    # ── Schema helpers ────────────────────────────────────────────────────────

    def _sessions_schema(self):
        import pyarrow as pa
        dim = self._embedder.dim
        return pa.schema([
            pa.field("session_id", pa.string()),
            pa.field("summary", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dim)),
            pa.field("binary_name", pa.string()),
            pa.field("project", pa.string()),
            pa.field("timestamp", pa.float64()),
            pa.field("tags", pa.string()),
        ])

    def _chunks_schema(self):
        import pyarrow as pa
        dim = self._embedder.dim
        return pa.schema([
            pa.field("chunk_id", pa.string()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dim)),
            pa.field("source_file", pa.string()),
            pa.field("offset", pa.int64()),
            pa.field("binary_name", pa.string()),
            pa.field("session_id", pa.string()),
            pa.field("timestamp", pa.float64()),
        ])

    # ── Table access ──────────────────────────────────────────────────────────

    def _get_sessions_table(self):
        if self._sessions_tbl is None:
            try:
                self._sessions_tbl = self._global_db.open_table("sessions")
            except Exception:
                self._sessions_tbl = self._global_db.create_table(
                    "sessions", schema=self._sessions_schema()
                )
        return self._sessions_tbl

    def _get_chunks_table(self, session_id: str):
        if session_id not in self._chunk_tbls:
            from session_paths import rag_session_uri
            import lancedb
            uri = rag_session_uri(session_id)
            db = lancedb.connect(uri)
            self._chunk_dbs[session_id] = db
            try:
                tbl = db.open_table("chunks")
            except Exception:
                tbl = db.create_table("chunks", schema=self._chunks_schema())
            self._chunk_tbls[session_id] = tbl
        return self._chunk_tbls[session_id]

    # ── RagService implementation ─────────────────────────────────────────────

    def index_session(self, session_id: str, summary: str, metadata: dict) -> None:
        try:
            vec = self._embedder.embed(summary)
            tbl = self._get_sessions_table()
            # delete existing row for this session, then insert fresh
            try:
                tbl.delete(f"session_id = '{session_id}'")
            except Exception:
                pass
            tbl.add([{
                "session_id": session_id,
                "summary": summary[:1200],
                "vector": vec,
                "binary_name": metadata.get("binary_name", "") or "",
                "project": metadata.get("project", "") or "",
                "timestamp": float(metadata.get("timestamp", time.time())),
                "tags": metadata.get("tags", "[]") or "[]",
            }])
            logger.info(f"  rag: indexed session {session_id}")
        except Exception as e:
            logger.warning(f"  rag: index_session failed — {e}")

    def index_chunks(self, session_id: str, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        try:
            texts = [c.text for c in chunks]
            vecs = self._embedder.embed_batch(texts)
            now = time.time()
            tbl = self._get_chunks_table(session_id)
            rows = []
            for chunk, vec in zip(chunks, vecs):
                rows.append({
                    "chunk_id": chunk.chunk_id,
                    "text": chunk.text,
                    "vector": vec,
                    "source_file": chunk.source_file,
                    "offset": chunk.offset,
                    "binary_name": chunk.binary_name,
                    "session_id": session_id,
                    "timestamp": now,
                })
            tbl.add(rows)
            logger.info(f"  rag: indexed {len(chunks)} chunks for session {session_id}")
            _emit_rag_event(
                "rag.index.updated",
                payload={"session_id": session_id, "n_chunks": len(chunks)},
            )
        except Exception as e:
            logger.warning(f"  rag: index_chunks failed — {e}")

    def query_global(self, query: str, top_k: int, threshold: float) -> list[SessionHit]:
        try:
            vec = self._embedder.embed(query)
            tbl = self._get_sessions_table()
            results = (
                tbl.search(vec)
                .metric("cosine")
                .limit(top_k)
                .to_list()
            )
            hits = []
            for row in results:
                score = max(0.0, 1.0 - float(row.get("_distance", 1.0)))
                if score < threshold:
                    continue
                hits.append(SessionHit(
                    session_id=row["session_id"],
                    summary=row["summary"],
                    score=score,
                    created_at=float(row.get("timestamp", 0.0)),
                    binary_name=row.get("binary_name", "") or "",
                    project=row.get("project", "") or "",
                ))
            return hits
        except Exception as e:
            logger.warning(f"  rag: query_global failed — {e}")
            return []

    def query_session(
        self, session_id: str, query: str, top_k: int, threshold: float
    ) -> list[ChunkHit]:
        _emit_rag_event(
            "rag.query.issued",
            payload={"scope": "session", "session_id": session_id, "top_k": top_k},
            content={"query": query},
        )
        try:
            vec = self._embedder.embed(query)
            tbl = self._get_chunks_table(session_id)
            results = (
                tbl.search(vec)
                .metric("cosine")
                .limit(top_k)
                .to_list()
            )
            hits = []
            for row in results:
                score = max(0.0, 1.0 - float(row.get("_distance", 1.0)))
                if threshold > 0.0 and score < threshold:
                    continue
                hits.append(ChunkHit(
                    chunk_id=row["chunk_id"],
                    text=row["text"],
                    source_file=row.get("source_file", "") or "",
                    score=score,
                    session_id=row.get("session_id", session_id),
                    binary_name=row.get("binary_name", "") or "",
                    offset=int(row.get("offset", 0)),
                ))
            _emit_rag_event(
                "rag.query.returned",
                payload={"scope": "session", "session_id": session_id, "n_hits": len(hits)},
                content={
                    "query": query,
                    "hits": [
                        {"chunk_id": h.chunk_id, "score": h.score,
                         "source_file": h.source_file, "text": h.text}
                        for h in hits
                    ],
                },
            )
            return hits
        except Exception as e:
            logger.warning(f"  rag: query_session({session_id}) failed — {e}")
            return []


def _emit_rag_event(event_type: str, *, payload: dict, content: dict | None = None) -> None:
    """Best-effort RAG telemetry — never raise."""
    try:
        from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
        get_event_bus().emit(RuntimeEvent(
            event_type,
            get_runtime_identity(),
            payload=payload,
            content=content or {},
            stage="RAG",
        ))
    except Exception:
        pass

    def build_context_block(
        self, query: str, current_session_id: str, budget_chars: int
    ) -> str:
        now = time.time()
        # (score, label, text) tuples — current session always included, no threshold gate
        candidates: list[tuple[float, str, str]] = []

        current_chunks = self.query_session(
            current_session_id, query, top_k=self._cfg.top_k, threshold=0.0
        )
        for c in current_chunks:
            label = f"This session · {c.source_file}" if c.source_file else "This session"
            candidates.append((c.score, label, c.text))

        past_sessions = self.query_global(
            query, top_k=3, threshold=self._cfg.threshold
        )
        for sh in past_sessions:
            if sh.session_id == current_session_id:
                continue
            past_chunks = self.query_session(
                sh.session_id, query, top_k=2, threshold=self._cfg.threshold + 0.05
            )
            days_ago = (now - sh.created_at) / 86400 if sh.created_at else 30
            decay = 0.9 ** (days_ago / 30)
            for c in past_chunks:
                label = (
                    f"Session {sh.session_id[:12]}… · {int(days_ago)}d ago"
                    + (f" · {c.source_file}" if c.source_file else "")
                )
                candidates.append((c.score * decay, label, c.text))

        if not candidates:
            return ""

        candidates.sort(key=lambda x: x[0], reverse=True)

        lines = ["--- Historical context ---"]
        used = len("--- Historical context ---\n---\n")
        for _, label, text in candidates:
            excerpt = text[:400].strip()
            entry = f"[{label}]\n  {excerpt}\n\n"
            if used + len(entry) > budget_chars:
                break
            lines.append(entry)
            used += len(entry)

        if len(lines) == 1:
            return ""

        lines.append("---")
        return "\n".join(lines)
