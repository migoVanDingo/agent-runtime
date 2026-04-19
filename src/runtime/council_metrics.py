"""Council metrics persistence.

Writes one JSON line per council run to _metrics/<session_id>.jsonl.
Tracks per-councillor decisions, synthesis outcomes, and optional user overrides.

Phase 1: record_run() only.
Phase 4: record_user_outcome() and user-gate integration.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from logger import get_logger

if TYPE_CHECKING:
    from runtime.council import CouncilRunMetrics

logger = get_logger(__name__)

METRICS_DIR = Path(__file__).resolve().parent.parent.parent / "_metrics"

_writer_instance: "CouncilMetricsWriter | None" = None


def init_metrics_writer(session_id: str) -> "CouncilMetricsWriter":
    """Initialize the singleton metrics writer for this session."""
    global _writer_instance
    _writer_instance = CouncilMetricsWriter(session_id)
    return _writer_instance


def get_metrics_writer() -> "CouncilMetricsWriter | None":
    """Return the active metrics writer, or None if not initialized."""
    return _writer_instance


class CouncilMetricsWriter:
    """Appends structured council run records to a per-session JSONL file."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        METRICS_DIR.mkdir(exist_ok=True)
        self._path = METRICS_DIR / f"{session_id}.jsonl"
        logger.info(f"  metrics: writing to {self._path}")

    def record_run(self, metrics: CouncilRunMetrics) -> None:
        """Append a council run record to the JSONL file."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "run_id": metrics.run_id,
            "context": metrics.context,
            "query": metrics.query[:200] if metrics.query else "",
            "mode": metrics.mode,
            "rounds_completed": metrics.rounds_completed,
            "councillors": metrics.councillor_labels,
            "decisions": metrics.per_councillor_decisions,
            "agreement_map": metrics.agreement_map,
            "synthesis_trace": metrics.synthesis_trace,
            "final_verdict": metrics.final_verdict,
            "user_outcome": metrics.user_outcome,
        }
        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as e:
            logger.warning(f"  metrics: failed to write record — {e}")

    def record_user_outcome(self, run_id: str, user_action: str, sided_with: list[str], overrode: list[str]) -> None:
        """Update an existing run record with the user's decision.

        Reads the file, finds the matching run_id, updates user_outcome, rewrites.
        Only called when a user is actually polled after a council decision.
        """
        outcome = {
            "user_action": user_action,
            "sided_with": sided_with,
            "overrode": overrode,
        }
        try:
            lines = self._path.read_text().splitlines()
            updated = []
            for line in lines:
                try:
                    record = json.loads(line)
                    if record.get("run_id") == run_id:
                        record["user_outcome"] = outcome
                        logger.info(
                            f"  metrics: user outcome for {run_id} — "
                            f"sided_with={sided_with} overrode={overrode}"
                        )
                    updated.append(json.dumps(record))
                except json.JSONDecodeError:
                    updated.append(line)
            self._path.write_text("\n".join(updated) + "\n")
        except OSError as e:
            logger.warning(f"  metrics: failed to record user outcome — {e}")
