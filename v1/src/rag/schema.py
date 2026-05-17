from __future__ import annotations
from dataclasses import dataclass, field
import uuid


@dataclass
class Chunk:
    text: str
    source_file: str = ""
    offset: int = 0
    binary_name: str = ""
    chunk_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class SessionHit:
    session_id: str
    summary: str
    score: float
    created_at: float = 0.0
    binary_name: str = ""
    project: str = ""


@dataclass
class ChunkHit:
    chunk_id: str
    text: str
    source_file: str
    score: float
    session_id: str = ""
    binary_name: str = ""
    offset: int = 0
