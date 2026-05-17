"""Dataclasses for ArtifactStore public API."""
from __future__ import annotations

from dataclasses import dataclass


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
