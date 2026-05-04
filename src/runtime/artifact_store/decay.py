"""Decay scoring and artifact tagging for ArtifactStore."""
from __future__ import annotations
import time
from runtime.artifact_store.types import ArtifactMeta
from logger import get_logger
logger = get_logger(__name__)

class _DecayMixin:

    def load_artifact_meta_for_session(self, session_id: str) -> int:
        rows = self._conn.execute(
            """SELECT key, kind, summary, source, session_id, created_at,
                      last_accessed, access_count, decay_score, permanent,
                      value, data_path
               FROM artifacts WHERE session_id = ?""",
            (session_id,),
        ).fetchall()
        count = 0
        for row in rows:
            self._meta[row["key"]] = ArtifactMeta(
                key=row["key"],
                kind=row["kind"],
                summary=row["summary"] or "",
                source=row["source"] or "",
                session_id=row["session_id"],
                created_at=row["created_at"],
                last_accessed=row["last_accessed"],
                access_count=row["access_count"],
                decay_score=row["decay_score"],
                permanent=bool(row["permanent"]),
                has_value=row["value"] is not None,
                has_data_path=row["data_path"] is not None,
                data_path=row["data_path"],
            )
            count += 1
        return count

    def set_tag(self, key: str, tag: str, value: str = "1") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO artifact_tags (key, tag, value) VALUES (?, ?, ?)",
            (key, tag, value),
        )
        self._conn.commit()

    def get_tag(self, key: str, tag: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM artifact_tags WHERE key = ? AND tag = ?",
            (key, tag),
        ).fetchone()
        if row is None:
            return None
        return row["value"]

    def apply_decay(self, factor: float = 0.85, threshold: float = 0.1) -> list[str]:
        if factor <= 0:
            return []
        archived: list[str] = []
        rows = self._conn.execute(
            "SELECT key, decay_score, permanent FROM artifacts WHERE session_id != ?",
            (self._session_id,),
        ).fetchall()
        for row in rows:
            is_permanent = bool(row["permanent"])
            # Pinned memory decays and archives more slowly than normal artifacts.
            eff_factor = factor
            eff_threshold = threshold
            if is_permanent:
                eff_factor = min(0.995, 1.0 - ((1.0 - factor) * 0.35))
                eff_threshold = threshold * 0.5

            new_score = float(row["decay_score"]) * eff_factor
            self._conn.execute(
                "UPDATE artifacts SET decay_score = ? WHERE key = ?",
                (new_score, row["key"]),
            )
            if new_score < eff_threshold:
                self.set_tag(row["key"], "archived", "1")
                archived.append(row["key"])
        self._conn.commit()
        return archived

