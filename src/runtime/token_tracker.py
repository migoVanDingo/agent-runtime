"""Session-level token usage tracker.

Accumulates input/output token counts per label (stage or component) and
logs a formatted summary at session end. All LLM calls pass a label through
provider.chat(label=...) which is logged here automatically.

Usage:
    from runtime.token_tracker import get_tracker
    tracker = get_tracker()
    tracker.log_summary()   # call at session end
    tracker.reset()         # call at session start if reusing
"""
from __future__ import annotations
from dataclasses import dataclass, field
from collections import defaultdict
from logger import get_logger

logger = get_logger(__name__)

_W = 56  # banner width matching runtime.utils


@dataclass
class _LabelStats:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class TokenTracker:
    """Accumulates token usage across all LLM calls in a session."""

    def __init__(self) -> None:
        self._stats: dict[str, _LabelStats] = defaultdict(_LabelStats)
        self._session_input = 0
        self._session_output = 0
        self._session_calls = 0

    def record(self, model: str, label: str, input_tokens: int, output_tokens: int) -> None:
        """Record a single LLM call. Called by providers automatically."""
        key = label or "unknown"
        s = self._stats[key]
        s.calls += 1
        s.input_tokens += input_tokens
        s.output_tokens += output_tokens
        self._session_input += input_tokens
        self._session_output += output_tokens
        self._session_calls += 1
        logger.info(
            f"  tokens [{key}] {model}: "
            f"in={input_tokens:,}  out={output_tokens:,}  "
            f"total={input_tokens + output_tokens:,}"
        )

    def log_summary(self) -> None:
        """Log per-label and session totals. Call at session end."""
        if self._session_calls == 0:
            return

        sep = "─" * _W
        logger.info(sep)
        logger.info("  Token Usage Summary")
        logger.info(sep)

        # Sort by total tokens descending
        rows = sorted(
            self._stats.items(),
            key=lambda kv: kv[1].input_tokens + kv[1].output_tokens,
            reverse=True,
        )
        for label, s in rows:
            total = s.input_tokens + s.output_tokens
            logger.info(
                f"  {label:<30}  calls={s.calls:>3}  "
                f"in={s.input_tokens:>7,}  out={s.output_tokens:>7,}  "
                f"total={total:>8,}"
            )

        logger.info(sep)
        session_total = self._session_input + self._session_output
        logger.info(
            f"  {'SESSION TOTAL':<30}  calls={self._session_calls:>3}  "
            f"in={self._session_input:>7,}  out={self._session_output:>7,}  "
            f"total={session_total:>8,}"
        )
        logger.info(sep)

    def reset(self) -> None:
        self._stats.clear()
        self._session_input = 0
        self._session_output = 0
        self._session_calls = 0


# Module-level singleton — one tracker per process lifetime.
_tracker = TokenTracker()


def get_tracker() -> TokenTracker:
    return _tracker
