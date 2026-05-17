"""TruncationStrategy — drop-oldest packing.

The baseline: walk newest → oldest, keep messages until the token budget is
exhausted. Optionally preserves the first user message (task definition) so
the agent never loses the original goal. Tool_use/tool_result pairs are
treated as atomic — either both kept or both dropped.

Config (``runtime.context.params.truncate``):
    budget_tokens: int       (default 30_000)
    keep_first_user: bool    (default True)
"""
from __future__ import annotations

import time
from typing import Any

from logger import get_logger
from runtime.context.packing import detect_tool_pairs
from runtime.context.scoring import estimate_tokens, message_text

logger = get_logger(__name__)


class TruncationStrategy:
    name = "truncate"

    def __init__(self, params: dict | None = None) -> None:
        params = params or {}
        self._budget = int(params.get("budget_tokens", 30_000))
        self._keep_first_user = bool(params.get("keep_first_user", True))

    # ── Strategy protocol no-ops ──────────────────────────────────────

    def set_summarizer(self, provider) -> None:
        del provider

    def set_importance(self, message_index: int, importance) -> None:
        del message_index, importance

    def get_importance(self, message_index: int):
        del message_index
        return None

    # ── Packing ───────────────────────────────────────────────────────

    def pack(
        self,
        messages: list[dict],
        current_query: str,
        plan_start_index: int | None = None,
        *,
        system_prompt_size: int = 0,
    ) -> list[dict]:
        del current_query, plan_start_index, system_prompt_size  # truncation ignores these
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
                "over_budget": total > self._budget,
            }))

        if total <= self._budget:
            if bus is not None:
                bus.emit(_pack_event("context.pack.completed", identity, payload={
                    "strategy": self.name,
                    "n_messages_out": len(messages),
                    "output_token_estimate": total,
                    "packed": False,
                }, duration_ms=int((time.monotonic() - t0) * 1000)))
            return messages

        pairs = detect_tool_pairs(messages)
        keep: set[int] = set()
        budget = self._budget

        # Always preserve the first user-text message when configured.
        if self._keep_first_user:
            for i, m in enumerate(messages):
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    cost = estimate_tokens(m["content"])
                    if cost <= budget:
                        keep.add(i)
                        budget -= cost
                    break

        # Walk newest → oldest. Skip pair partners that we've already accepted.
        n = len(messages)
        for i in range(n - 1, -1, -1):
            if i in keep:
                continue
            if i in pairs and pairs[i] in keep:
                # Partner already accepted in a prior iteration; nothing to do here.
                continue
            if i in pairs:
                partner = pairs[i]
                cost = (
                    estimate_tokens(message_text(messages[i]))
                    + estimate_tokens(message_text(messages[partner]))
                )
                if cost <= budget:
                    keep.add(i)
                    keep.add(partner)
                    budget -= cost
            else:
                cost = estimate_tokens(message_text(messages[i]))
                if cost <= budget:
                    keep.add(i)
                    budget -= cost

        packed = [m for i, m in enumerate(messages) if i in keep]
        packed_tokens = _sum_tokens(packed)
        logger.info(
            f"  truncate: kept {len(packed)}/{len(messages)} messages "
            f"({packed_tokens}/{self._budget} tokens)"
        )

        if bus is not None:
            bus.emit(_pack_event("context.pack.completed", identity, payload={
                "strategy": self.name,
                "n_messages_out": len(packed),
                "output_token_estimate": packed_tokens,
                "n_dropped": len(messages) - len(packed),
                "packed": True,
            }, duration_ms=int((time.monotonic() - t0) * 1000)))
        return packed


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
