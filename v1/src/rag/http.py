"""HttpRagService — HTTP client to a containerized RAG service.

Implements the same RagService interface as LocalRagService.
Switch by setting rag.mode = http and rag.http_base_url in config.yml.

The RAG service's FastAPI app exposes these endpoints and delegates
to a LocalRagService internally. No agent-side code changes when switching.

Default port: 17433 (configurable via rag.http_base_url).
"""
from __future__ import annotations

import dataclasses
from typing import Any

from logger import get_logger
from rag.schema import Chunk, SessionHit, ChunkHit
from rag.service import RagService

logger = get_logger(__name__)


class HttpRagService(RagService):
    def __init__(self, base_url: str) -> None:
        import httpx
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=15.0)
        logger.info(f"  rag: using remote service at {base_url}")

    def _post(self, path: str, payload: dict) -> Any:
        try:
            r = self._client.post(path, json=payload)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning(f"  rag: HTTP call {path} failed — {e}")
            return None

    def index_session(self, session_id: str, summary: str, metadata: dict) -> None:
        self._post("/index/session", {
            "session_id": session_id,
            "summary": summary,
            "metadata": metadata,
        })

    def index_chunks(self, session_id: str, chunks: list[Chunk]) -> None:
        self._post("/index/chunks", {
            "session_id": session_id,
            "chunks": [dataclasses.asdict(c) for c in chunks],
        })

    def query_global(self, query: str, top_k: int, threshold: float) -> list[SessionHit]:
        data = self._post("/query/global", {"query": query, "top_k": top_k, "threshold": threshold})
        if not data:
            return []
        return [SessionHit(**h) for h in data]

    def query_session(
        self, session_id: str, query: str, top_k: int, threshold: float
    ) -> list[ChunkHit]:
        data = self._post("/query/session", {
            "session_id": session_id,
            "query": query,
            "top_k": top_k,
            "threshold": threshold,
        })
        if not data:
            return []
        return [ChunkHit(**c) for c in data]

    def build_context_block(
        self, query: str, current_session_id: str, budget_chars: int
    ) -> str:
        data = self._post("/context", {
            "query": query,
            "current_session_id": current_session_id,
            "budget_chars": budget_chars,
        })
        if not data:
            return ""
        return data.get("block", "")
