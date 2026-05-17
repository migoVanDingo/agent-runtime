"""ContextManager — the ``afm`` context-packing strategy.

This is the default strategy: AFM-inspired non-destructive packing using
semantic similarity, recency decay, and rule-based importance to assign
fidelity tiers (FULL / COMPRESSED / PLACEHOLDER) before chronological
chronological packing. The Messenger remains the source of truth; the
strategy produces a per-call view.

Configuration lives under ``runtime.context.params.afm`` in config.yml.
Legacy ``runtime.context_manager.*`` keys are translated by the loader
(see ``config.loader._load_context_config_with_compat``).

Other strategies live in ``runtime/context/strategies/``.
"""
from __future__ import annotations

import time

from runtime.schema import Importance
from runtime.context.scoring import score_messages, message_text, estimate_tokens
from runtime.context.fidelity import assign_fidelity
from runtime.context.packing import pack_chronological
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


# Defaults — mirror the legacy ContextManagerConfig so behavior is unchanged
# when a config file supplies only the old keys (or no keys at all).
_DEFAULTS = {
    "enabled": True,
    "message_budget_tokens": 65536,
    # Smaller cap applied when ``runtime.scope.current_scope() == "runtime"``.
    # Stops haiku-class classifier calls (RoutingStage, SkillHintStage,
    # ExecutionMonitor, ImportanceScorer) from blowing past the runtime
    # provider's per-minute rate limit when conversation history is large.
    # See _plans/0090-context-discipline-and-subagents.md §6 0090a.
    "runtime_message_budget_tokens": 12000,
    "half_life_turns": 15,
    "threshold_high": 0.35,
    "threshold_mid": 0.20,
    "compressed_max_chars": 800,
}


class ContextManager:
    """The AFM context-packing strategy."""

    name = "afm"

    def __init__(self, params: dict | None = None, embedding_model=None):
        """Construct the strategy.

        ``params`` is the per-strategy config block (``runtime.context.params.afm``).
        When None, falls back to ``config.runtime.context_manager`` for back-compat.
        ``embedding_model`` is accepted for legacy callers; the shared embedding
        model from ``embeddings`` is used at lookup time regardless.
        """
        del embedding_model  # legacy arg — kept for back-compat
        params = dict(params or {})
        legacy = getattr(config.runtime, "context_manager", None)

        def _resolve(key: str):
            if key in params:
                return params[key]
            if legacy is not None and hasattr(legacy, key):
                return getattr(legacy, key)
            return _DEFAULTS[key]

        self._enabled = bool(_resolve("enabled"))
        self._budget = int(_resolve("message_budget_tokens"))
        self._runtime_budget = int(_resolve("runtime_message_budget_tokens"))
        self._half_life = int(_resolve("half_life_turns"))
        self._threshold_high = float(_resolve("threshold_high"))
        self._threshold_mid = float(_resolve("threshold_mid"))
        self._compressed_max = int(_resolve("compressed_max_chars"))

        self._model = None  # lazy-loaded shared embedding model
        # LLM-assigned importance overrides (message_index -> Importance)
        self._importance_overrides: dict[int, Importance] = {}
        # LLM summarization cache (content_hash -> summary)
        self._summary_cache: dict[str, str] = {}
        self._summarizer = None

    # ── Strategy protocol ─────────────────────────────────────────────

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

    def pack(
        self,
        messages: list[dict],
        current_query: str,
        plan_start_index: int | None = None,
        *,
        system_prompt_size: int = 0,
    ) -> list[dict]:
        """Return a budget-constrained version of the message history.

        Args:
            plan_start_index: if set, messages from this index onward are part of
                the current plan execution and will be boosted in importance.
            system_prompt_size: estimated tokens that will be sent in the
                ``system`` parameter of the upcoming LLM call. AFM packs to
                ``effective_budget = total_budget - system_prompt_size`` so
                the whole call respects one cap. Default 0 keeps old behavior
                for callers that don't know their system prompt size.

        The selected budget depends on ``runtime.scope.current_scope()``:
            - ``"runtime"`` (RoutingStage, SkillHintStage, ExecutionMonitor,
              ImportanceScorer): uses ``runtime_message_budget_tokens``
              (default 12000). Stops haiku-class classifier calls from
              exceeding per-minute rate limits as conversation history grows.
            - anything else (``"main"``, ``"subagent:*"``): uses the full
              ``message_budget_tokens`` (default 65536).

        See _plans/0090-context-discipline-and-subagents.md §6 0090a.
        """
        if not self._enabled:
            return messages

        if not messages:
            return messages

        from runtime.scope import current_scope, RUNTIME
        scope = current_scope()
        total_budget = self._runtime_budget if scope == RUNTIME else self._budget
        effective_budget = max(1000, total_budget - max(0, system_prompt_size))

        if system_prompt_size > 0 and system_prompt_size > total_budget // 2:
            logger.warning(
                f"  context_manager: system prompt is {system_prompt_size} tokens, "
                f">50% of {scope} budget {total_budget}. Effective message budget reduced "
                f"to {effective_budget}. Consider trimming the system prompt."
            )

        total = sum(estimate_tokens(message_text(m)) for m in messages)
        bus, identity = _bus_and_identity()
        t0 = time.monotonic()
        if bus is not None:
            bus.emit(_pack_event(
                "context.pack.started", identity,
                payload={
                    "strategy": self.name,
                    "scope": scope,
                    "n_messages_in": len(messages),
                    "input_token_estimate": total,
                    "system_prompt_size": system_prompt_size,
                    "total_budget": total_budget,
                    "effective_budget": effective_budget,
                    "plan_start_index": plan_start_index,
                    "over_budget": total > effective_budget,
                },
            ))

        if total <= effective_budget:
            if bus is not None:
                bus.emit(_pack_event(
                    "context.pack.completed", identity,
                    payload={
                        "strategy": self.name,
                        "scope": scope,
                        "n_messages_out": len(messages),
                        "output_token_estimate": total,
                        "system_prompt_size": system_prompt_size,
                        "packed": False,
                    },
                    duration_ms=int((time.monotonic() - t0) * 1000),
                ))
            return messages

        logger.info(
            f"  context_manager: {total} tokens est. > effective budget {effective_budget} "
            f"(scope={scope}, sys={system_prompt_size}) — packing"
        )

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
            budget=effective_budget,
            max_chars=self._compressed_max,
            summarizer=self._summarizer,
            summary_cache=self._summary_cache,
        )

        packed_total = sum(s.token_estimate for s in packed)
        fidelity_counts = {}
        for s in packed:
            fidelity_counts[s.fidelity.value] = fidelity_counts.get(s.fidelity.value, 0) + 1
        logger.info(f"  context_manager: packed to {packed_total} tokens — {fidelity_counts}")

        if bus is not None:
            bus.emit(_pack_event(
                "context.pack.completed", identity,
                payload={
                    "strategy": self.name,
                    "scope": scope,
                    "n_messages_out": len(packed),
                    "output_token_estimate": packed_total,
                    "system_prompt_size": system_prompt_size,
                    "fidelity_counts": fidelity_counts,
                    "n_dropped": len(scored) - len(packed),
                    "packed": True,
                },
                duration_ms=int((time.monotonic() - t0) * 1000),
            ))

        return [s.message for s in packed]


def _bus_and_identity():
    try:
        from runtime.events import get_event_bus, get_runtime_identity
        return get_event_bus(), get_runtime_identity()
    except Exception:
        return None, None


def _pack_event(event_type, identity, *, payload, duration_ms=None):
    from runtime.events import RuntimeEvent
    return RuntimeEvent(
        event_type,
        identity,
        payload=payload,
        stage="ContextManager",
        duration_ms=duration_ms,
    )
