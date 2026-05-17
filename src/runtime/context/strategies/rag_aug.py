"""RagAugmentedStrategy — pack semantically relevant past messages.

For each ``pack()`` call:

1. The last ``keep_last_n`` messages are kept verbatim (the immediate
   working set the agent needs to react to).
2. The first user message is preserved (task definition).
3. The remaining older messages are scored by cosine similarity between
   their text and the current query. Messages above ``score_threshold``,
   capped at ``top_k``, are retained in chronological order.
4. Tool_use/tool_result pairs are kept atomic — if either half is selected
   the other is pulled in too.

Falls back to keeping the recent window if no embedding model or query is
available.

Config (``runtime.context.params.rag``):
    budget_tokens: int       (default 30_000) — informational; this strategy
                                                doesn't enforce a budget by truncation
    top_k: int               (default 12)     — max older messages to retain
    score_threshold: float   (default 0.45)   — min cosine similarity to keep
    keep_last_n: int         (default 8)      — verbatim tail length
"""
from __future__ import annotations

import time
from typing import Any

from logger import get_logger
from runtime.context.packing import detect_tool_pairs
from runtime.context.scoring import estimate_tokens, message_text

logger = get_logger(__name__)


class RagAugmentedStrategy:
    name = "rag"

    def __init__(self, params: dict | None = None) -> None:
        params = params or {}
        self._budget = int(params.get("budget_tokens", 30_000))
        self._top_k = int(params.get("top_k", 12))
        self._threshold = float(params.get("score_threshold", 0.45))
        self._keep_last_n = max(1, int(params.get("keep_last_n", 8)))
        self._keep_first_user = bool(params.get("keep_first_user", True))
        self._model = None

    # ── Strategy protocol no-ops ──────────────────────────────────────

    def set_summarizer(self, provider) -> None:
        del provider

    def set_importance(self, message_index: int, importance) -> None:
        del message_index, importance

    def get_importance(self, message_index: int):
        del message_index
        return None

    def _embedding_model(self):
        if self._model is None:
            try:
                from embeddings import get_embedding_model
                self._model = get_embedding_model()
            except Exception as exc:
                logger.warning(f"  rag: embedding model unavailable ({exc!r}); falling back to tail-only")
                self._model = False  # sentinel: don't retry
        return self._model or None

    # ── Packing ───────────────────────────────────────────────────────

    def pack(
        self,
        messages: list[dict],
        current_query: str,
        plan_start_index: int | None = None,
        *,
        system_prompt_size: int = 0,
    ) -> list[dict]:
        del plan_start_index, system_prompt_size  # RAG selection is query-driven; plan window doesn't apply
        if not messages:
            return messages

        total = _sum_tokens(messages)
        bus, identity = _bus_and_identity()
        t0 = time.monotonic()
        if bus is not None:
            bus.emit(_pack_event("context.pack.started", identity, payload={
                "strategy": self.name,
                "n_messages_in": len(messages),
                "input_token_estimate": total,
                "budget": self._budget,
                "top_k": self._top_k,
                "score_threshold": self._threshold,
            }))

        # Recent tail — always kept.
        split = max(0, len(messages) - self._keep_last_n)
        recent = messages[split:]
        older = messages[:split]

        # Find first-user index inside older for explicit preservation.
        first_user_idx: int | None = None
        if self._keep_first_user and older:
            for i, m in enumerate(older):
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    first_user_idx = i
                    break

        kept_idxs: set[int] = set()
        if first_user_idx is not None:
            kept_idxs.add(first_user_idx)

        # Score remaining older messages by cosine similarity.
        model = self._embedding_model() if older and current_query else None
        scored: list[tuple[int, float]] = []
        if model is not None:
            try:
                query_emb = model.encode(current_query, show_progress_bar=False)
                from sklearn.metrics.pairwise import cosine_similarity
                for i, msg in enumerate(older):
                    if i == first_user_idx:
                        continue
                    text = message_text(msg)[:500]
                    if not text:
                        continue
                    emb = model.encode(text, show_progress_bar=False)
                    sim = float(cosine_similarity([query_emb], [emb])[0][0])
                    scored.append((i, max(0.0, sim)))
            except Exception as exc:
                logger.warning(f"  rag: scoring failed ({exc!r}); falling back to tail-only")
                scored = []

        # Pick top-K above threshold.
        scored.sort(key=lambda p: p[1], reverse=True)
        for idx, score in scored[: self._top_k]:
            if score < self._threshold:
                break
            kept_idxs.add(idx)

        # Enforce tool-pair atomicity.
        pairs = detect_tool_pairs(older)
        for idx in list(kept_idxs):
            partner = pairs.get(idx)
            if partner is not None:
                kept_idxs.add(partner)

        # Re-assemble chronologically.
        kept_older = [older[i] for i in sorted(kept_idxs)]
        result = kept_older + recent

        packed_tokens = _sum_tokens(result)
        logger.info(
            f"  rag: kept {len(kept_older)} relevant + {len(recent)} recent "
            f"of {len(messages)} ({packed_tokens} tokens)"
        )

        if bus is not None:
            bus.emit(_pack_event("context.pack.completed", identity, payload={
                "strategy": self.name,
                "n_messages_out": len(result),
                "output_token_estimate": packed_tokens,
                "n_dropped": len(messages) - len(result),
                "n_recent_kept": len(recent),
                "n_older_kept": len(kept_older),
                "packed": True,
            }, duration_ms=int((time.monotonic() - t0) * 1000)))
        return result


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sum_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(message_text(m)) for m in messages)


def _bus_and_identity():
    try:
        from runtime.events import get_event_bus, get_runtime_identity
        return get_event_bus(), get_runtime_identity()
    except Exception:
        return None, None


def _pack_event(event_type: str, identity, *, payload: dict[str, Any], duration_ms: int | None = None):
    from runtime.events import RuntimeEvent
    return RuntimeEvent(
        event_type,
        identity,
        payload=payload,
        stage="ContextStrategy",
        duration_ms=duration_ms,
    )
