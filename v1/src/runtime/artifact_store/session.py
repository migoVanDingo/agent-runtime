"""Session lifecycle and conversation persistence methods for ArtifactStore."""
from __future__ import annotations
import json
import time
from runtime.artifact_store.types import ArtifactMeta, ResumableSession
from logger import get_logger
logger = get_logger(__name__)

class _SessionMixin:
    def init_session(self, session_id: str) -> None:
        """Register new session and set active session ID."""
        self._session_id = session_id
        now = time.time()
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, started_at, ended_at, resumable, artifact_count) "
            "VALUES (?, ?, NULL, 1, COALESCE((SELECT artifact_count FROM sessions WHERE session_id = ?), 0))",
            (session_id, now, session_id),
        )
        self._conn.commit()
        logger.debug(f"ArtifactStore: session {session_id} initialized")

    def load_session(self, resume_id: str) -> str:
        """Load existing resumable session as active session."""
        row = self._conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = ? AND resumable = 1 AND ended_at IS NULL",
            (resume_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Session '{resume_id}' is not resumable or does not exist")

        self._session_id = row["session_id"]
        self._cache.clear()
        self._meta.clear()
        self._dirty.clear()
        self.load_artifact_meta_for_session(self._session_id)
        logger.info(f"ArtifactStore: resumed session {self._session_id}")
        return self._session_id

    def mark_detached(self, session_id: str | None = None) -> None:
        sid = session_id or self._session_id
        if not sid:
            return
        self._conn.execute(
            "UPDATE sessions SET ended_at = NULL, resumable = 1 WHERE session_id = ?",
            (sid,),
        )
        self._conn.commit()

    def mark_closed(self, session_id: str | None = None) -> None:
        sid = session_id or self._session_id
        if not sid:
            return
        now = time.time()
        self._conn.execute(
            "UPDATE sessions SET ended_at = ?, resumable = 0 WHERE session_id = ?",
            (now, sid),
        )
        self._conn.commit()

    def set_active_project(self, project: str | None) -> None:
        p = (project or "").strip()
        self._active_project = p or None

    def get_active_project(self) -> str | None:
        return self._active_project

    def list_resumable_sessions(self, limit: int = 20) -> list[ResumableSession]:
        rows = self._conn.execute(
            "SELECT session_id, started_at, COALESCE(artifact_count, 0) AS artifact_count "
            "FROM sessions WHERE resumable = 1 AND ended_at IS NULL "
            "ORDER BY started_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()

        result: list[ResumableSession] = []
        for row in rows:
            sid = row["session_id"]
            preview = self._first_user_message_preview(sid) or "(no preview available)"
            result.append(
                ResumableSession(
                    session_id=sid,
                    started_at=float(row["started_at"]),
                    artifact_count=int(row["artifact_count"]),
                    preview=preview,
                )
            )
        return result

    def _first_user_message_preview(self, session_id: str) -> str | None:
        rows = self._conn.execute(
            "SELECT content FROM conversation_history "
            "WHERE session_id = ? AND role = 'user' ORDER BY turn ASC",
            (session_id,),
        ).fetchall()
        for row in rows:
            try:
                parsed = json.loads(row["content"])
            except Exception:
                continue
            if isinstance(parsed, str):
                text = parsed.strip().replace("\n", " ")
                if text:
                    return text[:160]
        return None

    # ── Conversation persistence ───────────────────────────────────────

    def save_conversation(self, messages: list[dict]) -> int:
        if not self._session_id:
            return 0
        now = time.time()
        cur = self._conn.cursor()
        cur.execute("DELETE FROM conversation_history WHERE session_id = ?", (self._session_id,))
        inserted = 0
        for i, msg in enumerate(messages):
            role = str(msg.get("role", ""))
            content = json.dumps(msg.get("content"), ensure_ascii=False)
            cur.execute(
                "INSERT INTO conversation_history (session_id, role, content, turn, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (self._session_id, role, content, i, now),
            )
            inserted += 1
        self._conn.commit()
        return inserted

    def load_conversation(self, session_id: str | None = None) -> list[dict]:
        sid = session_id or self._session_id
        if not sid:
            return []

        rows = self._conn.execute(
            "SELECT role, content FROM conversation_history WHERE session_id = ? ORDER BY turn ASC",
            (sid,),
        ).fetchall()
        messages: list[dict] = []
        for row in rows:
            try:
                content = json.loads(row["content"])
            except Exception:
                logger.warning("ArtifactStore.load_conversation: skipping malformed row")
                continue
            messages.append({"role": row["role"], "content": content})
        return messages

