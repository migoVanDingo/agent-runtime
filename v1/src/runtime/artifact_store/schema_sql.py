"""SQL DDL constants, storage helpers, and math utilities for ArtifactStore."""
from __future__ import annotations

import json
from array import array
from typing import Any

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
