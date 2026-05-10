"""RagService — abstract interface for the two-tier RAG system.

The rest of the codebase only imports this interface. Concrete implementations:
  LocalRagService  — LanceDB in-process (local dev, single container)
  HttpRagService   — HTTP client to a containerized RAG service (production)

Swapping implementations is a config change: rag.mode = local | http
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from rag.schema import Chunk, SessionHit, ChunkHit


class RagService(ABC):
    @abstractmethod
    def index_session(self, session_id: str, summary: str, metadata: dict) -> None: ...

    @abstractmethod
    def index_chunks(self, session_id: str, chunks: list[Chunk]) -> None: ...

    @abstractmethod
    def query_global(self, query: str, top_k: int, threshold: float) -> list[SessionHit]: ...

    @abstractmethod
    def query_session(
        self, session_id: str, query: str, top_k: int, threshold: float
    ) -> list[ChunkHit]: ...

    @abstractmethod
    def build_context_block(
        self, query: str, current_session_id: str, budget_chars: int
    ) -> str: ...
