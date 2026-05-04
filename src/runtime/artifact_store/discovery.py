"""Request logging and workflow candidate discovery for ArtifactStore."""
from __future__ import annotations
import json
import time
from runtime.artifact_store.types import WorkflowCandidate, _RequestRow
from runtime.artifact_store.schema_sql import _vec_to_blob, _blob_to_vec, _cosine_similarity, _safe_int_list
from logger import get_logger
logger = get_logger(__name__)

class _DiscoveryMixin:

    def record_request(self, message: str, workflow: str | None = None) -> int | None:
        if not self._session_id:
            return None
        now = time.time()

        embedding_blob: bytes | None = None
        try:
            from embeddings import get_embedding_model

            model = get_embedding_model()
            emb = model.encode(message, show_progress_bar=False)
            if hasattr(emb, "tolist"):
                emb = emb.tolist()
            embedding_blob = _vec_to_blob([float(x) for x in emb])
        except Exception as e:
            logger.warning(f"ArtifactStore.record_request: embedding unavailable ({e})")

        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO requests (session_id, message, embedding, workflow, created_at) VALUES (?, ?, ?, ?, ?)",
            (self._session_id, message, embedding_blob, workflow, now),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def discover_workflows(
        self,
        lookback_days: int = 30,
        similarity_threshold: float = 0.82,
        frequency_threshold: int = 5,
        recency_decay: float = 0.95,
    ) -> list[WorkflowCandidate]:
        now = time.time()
        cutoff = now - max(1, int(lookback_days)) * 86400
        rows = self._conn.execute(
            "SELECT id, message, embedding, created_at FROM requests "
            "WHERE created_at >= ? AND embedding IS NOT NULL ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
        reqs: list[_RequestRow] = []
        for r in rows:
            try:
                emb = _blob_to_vec(r["embedding"])
            except Exception:
                continue
            reqs.append(
                _RequestRow(
                    id=int(r["id"]),
                    message=str(r["message"]),
                    embedding=emb,
                    created_at=float(r["created_at"]),
                )
            )

        if len(reqs) < max(2, frequency_threshold):
            return []

        n = len(reqs)
        adj: list[list[int]] = [[] for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                sim = _cosine_similarity(reqs[i].embedding, reqs[j].embedding)
                if sim >= similarity_threshold:
                    adj[i].append(j)
                    adj[j].append(i)

        visited = [False] * n
        clusters: list[list[int]] = []
        for i in range(n):
            if visited[i]:
                continue
            stack = [i]
            visited[i] = True
            comp = []
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for nxt in adj[cur]:
                    if not visited[nxt]:
                        visited[nxt] = True
                        stack.append(nxt)
            if len(comp) >= frequency_threshold:
                clusters.append(comp)

        discovered: list[WorkflowCandidate] = []
        for comp in clusters:
            comp_rows = [reqs[i] for i in comp]
            comp_rows.sort(key=lambda r: r.created_at, reverse=True)
            frequency = len(comp_rows)
            recency_score = 0.0
            for r in comp_rows:
                days_ago = max(0.0, (now - r.created_at) / 86400.0)
                recency_score += recency_decay ** days_ago

            example_ids = [r.id for r in comp_rows[:5]]
            exemplar = comp_rows[0].message.strip().replace("\n", " ")
            if len(exemplar) > 100:
                exemplar = exemplar[:97] + "..."
            description = f"Recurring requests similar to: '{exemplar}'"

            existing = self._conn.execute(
                "SELECT id FROM workflow_candidates WHERE description = ? AND status IN ('candidate', 'approved')",
                (description,),
            ).fetchone()
            if existing is None:
                cur = self._conn.cursor()
                cur.execute(
                    "INSERT INTO workflow_candidates "
                    "(name, description, example_ids, frequency, last_seen, recency_score, status, approved_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'candidate', NULL)",
                    (
                        None,
                        description,
                        json.dumps(example_ids),
                        frequency,
                        comp_rows[0].created_at,
                        recency_score,
                    ),
                )
                cid = int(cur.lastrowid)
                self._conn.commit()
            else:
                cid = int(existing["id"])
                self._conn.execute(
                    "UPDATE workflow_candidates SET example_ids = ?, frequency = ?, last_seen = ?, recency_score = ? "
                    "WHERE id = ?",
                    (
                        json.dumps(example_ids),
                        frequency,
                        comp_rows[0].created_at,
                        recency_score,
                        cid,
                    ),
                )
                self._conn.commit()

            c = self.get_workflow_candidate(cid)
            if c is not None:
                discovered.append(c)

        return discovered

    def get_workflow_candidate(self, candidate_id: int) -> WorkflowCandidate | None:
        row = self._conn.execute(
            "SELECT id, description, example_ids, frequency, last_seen, recency_score, status, approved_at "
            "FROM workflow_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        ids = _safe_int_list(row["example_ids"])
        messages = self._messages_for_request_ids(ids)
        return WorkflowCandidate(
            id=int(row["id"]),
            description=str(row["description"]),
            example_ids=ids,
            frequency=int(row["frequency"]),
            last_seen=float(row["last_seen"]),
            recency_score=float(row["recency_score"]),
            status=str(row["status"]),
            approved_at=float(row["approved_at"]) if row["approved_at"] is not None else None,
            example_messages=messages,
        )

    def get_pending_workflow_candidates(self, limit: int = 10) -> list[WorkflowCandidate]:
        rows = self._conn.execute(
            "SELECT id FROM workflow_candidates WHERE status = 'candidate' ORDER BY recency_score DESC, last_seen DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        out: list[WorkflowCandidate] = []
        for row in rows:
            c = self.get_workflow_candidate(int(row["id"]))
            if c is not None:
                out.append(c)
        return out

    def approve_workflow_candidate(self, candidate_id: int) -> None:
        self._conn.execute(
            "UPDATE workflow_candidates SET status = 'approved', approved_at = ? WHERE id = ?",
            (time.time(), candidate_id),
        )
        self._conn.commit()

    def reject_workflow_candidate(self, candidate_id: int) -> None:
        self._conn.execute(
            "UPDATE workflow_candidates SET status = 'rejected' WHERE id = ?",
            (candidate_id,),
        )
        self._conn.commit()

    def _messages_for_request_ids(self, ids: list[int]) -> list[str]:
        if not ids:
            return []
        out: list[str] = []
        for rid in ids:
            row = self._conn.execute("SELECT message FROM requests WHERE id = ?", (rid,)).fetchone()
            if row is None:
                continue
            msg = str(row["message"]).strip().replace("\n", " ")
            if len(msg) > 120:
                msg = msg[:117] + "..."
            out.append(msg)
        return out

