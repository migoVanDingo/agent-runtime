"""ContextManager facade — budget-constrained context packing.

The Messenger stores full history unchanged. Before each provider.chat() call,
the ContextManager produces a budget-constrained version by scoring each message
and assigning fidelity levels (FULL / COMPRESSED / PLACEHOLDER).
"""
from __future__ import annotations

from runtime.schema import Importance
from runtime.context.scoring import score_messages, message_text, estimate_tokens
from runtime.context.fidelity import assign_fidelity
from runtime.context.packing import pack_chronological
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


class ContextManager:

    def __init__(self, embedding_model=None):
        """
        Args:
            embedding_model: deprecated, ignored. Uses shared embedding model
                             from embeddings module. If embedding model is not
                             available, similarity scoring is disabled — only
                             recency and importance are used.
        """
        self._model = None  # lazy-loaded from shared embeddings
        cfg = config.runtime.context_manager
        self._budget = cfg.message_budget_tokens
        self._half_life = cfg.half_life_turns
        self._threshold_high = cfg.threshold_high
        self._threshold_mid = cfg.threshold_mid
        self._compressed_max = cfg.compressed_max_chars
        # LLM-assigned importance overrides (message_index -> Importance)
        self._importance_overrides: dict[int, Importance] = {}
        # LLM summarization cache (content_hash -> summary)
        self._summary_cache: dict[str, str] = {}
        self._summarizer = None

    def set_summarizer(self, provider) -> None:
        """Set the provider for LLM-based compression summarization."""
        self._summarizer = provider

    def set_importance(self, message_index: int, importance: Importance) -> None:
        """Set an LLM-assigned importance override for a message at the given index."""
        self._importance_overrides[message_index] = importance

    def get_importance(self, message_index: int) -> Importance | None:
        """Return the LLM-assigned importance for a message index, or None if not set."""
        return self._importance_overrides.get(message_index)

    def _embedding_model(self):
        """Lazy-load the shared embedding model."""
        if self._model is None:
            from embeddings import get_embedding_model
            self._model = get_embedding_model()
        return self._model

    def pack(self, messages: list[dict], current_query: str, plan_start_index: int | None = None) -> list[dict]:
        """Return a budget-constrained version of the message history.

        If total tokens are under budget, returns messages unchanged.

        Args:
            plan_start_index: if set, messages from this index onward are part of
                the current plan execution and will be boosted in importance.
        """
        if not config.runtime.context_manager.enabled:
            return messages

        if not messages:
            return messages

        total = sum(estimate_tokens(message_text(m)) for m in messages)
        if total <= self._budget:
            return messages

        logger.info(f"  context_manager: {total} tokens est. > budget {self._budget} — packing")

        scored = score_messages(
            messages, current_query, plan_start_index,
            embedding_model=self._embedding_model(),
            importance_overrides=self._importance_overrides,
            half_life=self._half_life,
        )
        assign_fidelity(
            scored,
            plan_start_index=plan_start_index,
            threshold_high=self._threshold_high,
            threshold_mid=self._threshold_mid,
        )
        packed = pack_chronological(
            scored,
            budget=self._budget,
            max_chars=self._compressed_max,
            summarizer=self._summarizer,
            summary_cache=self._summary_cache,
        )

        packed_total = sum(s.token_estimate for s in packed)
        fidelity_counts = {}
        for s in packed:
            fidelity_counts[s.fidelity.value] = fidelity_counts.get(s.fidelity.value, 0) + 1
        logger.info(f"  context_manager: packed to {packed_total} tokens — {fidelity_counts}")

        return [s.message for s in packed]
