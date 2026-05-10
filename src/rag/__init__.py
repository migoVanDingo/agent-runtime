"""Two-tier RAG system backed by LanceDB.

Public API — this is the only import the rest of the codebase needs:

    from rag import get_rag_service, init_rag_service

All callers guard with:

    if rag := get_rag_service():
        rag.index_chunks(session_id, chunks)

so the system degrades gracefully when rag.enabled=false or lancedb is not installed.
"""
from __future__ import annotations

from rag.service import RagService
from rag.schema import Chunk, SessionHit, ChunkHit

_service: RagService | None = None


def get_rag_service() -> RagService | None:
    return _service


def init_rag_service(session_id: str) -> RagService | None:
    global _service
    from app_config import config

    cfg = config.rag
    if not cfg.enabled:
        return None

    if cfg.mode == "http":
        if not cfg.http_base_url:
            from logger import get_logger
            get_logger(__name__).warning(
                "rag.mode=http but rag.http_base_url is not set — RAG disabled"
            )
            return None
        from rag.http import HttpRagService
        _service = HttpRagService(cfg.http_base_url)
        return _service

    # mode == "local"
    try:
        import lancedb  # noqa: F401 — verify installed before doing any work
    except ImportError:
        from logger import get_logger
        get_logger(__name__).warning(
            "rag.enabled=true but lancedb is not installed — RAG disabled. "
            "Run: pip install lancedb"
        )
        return None

    try:
        from rag.embedder import get_embedder
        from rag.local import LocalRagService
        from session_paths import rag_global_uri

        embedder = get_embedder(cfg.embedding_provider, cfg.embedding_model)
        _service = LocalRagService(embedder, rag_global_uri(), cfg)
        from logger import get_logger
        get_logger(__name__).info(
            f"  rag: LocalRagService ready "
            f"(provider={cfg.embedding_provider}, model={cfg.embedding_model}, "
            f"dim={embedder.dim})"
        )
        return _service
    except Exception as e:
        from logger import get_logger
        get_logger(__name__).warning(f"  rag: init failed — {e}")
        return None


__all__ = ["RagService", "Chunk", "SessionHit", "ChunkHit", "get_rag_service", "init_rag_service"]
