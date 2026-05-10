"""Embedding-based recall — replaced by src/rag/ (plan 0081).

This module is intentionally empty. The _RecallMixin and its SQLite-based
embedding tables have been removed. Session indexing and semantic recall
are now handled by LocalRagService / HttpRagService in src/rag/.
"""


class _RecallMixin:
    """Stub — no-op. Kept so ArtifactStore's MRO doesn't break during transition."""
