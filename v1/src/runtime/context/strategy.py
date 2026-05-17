"""ContextStrategy Protocol — the swappable boundary for context management.

Strategies are passive: they decide *which messages to send* to the next
provider call, but never drive control flow (no retries, no escalations).
See ``_plans/0079-runtime-as-god.md``, ``_plans/0089-pluggable-context-manager.md``,
and ``_plans/0090-context-discipline-and-subagents.md``.

Implementations register themselves via ``runtime.context.factory``. Each
strategy receives its config block as a plain ``params`` dict at construction
time, so adding new strategies doesn't require editing config schemas.

The ``pack`` signature accepts an optional ``system_prompt_size`` parameter
(token estimate of the system prompt that will accompany the messages). AFM
uses it to compute ``effective = total_budget - system_prompt_size`` so the
overall LLM call respects a single budget. Other strategies may ignore it.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from runtime.schema import Importance


@runtime_checkable
class ContextStrategy(Protocol):
    """Decides what messages to include before each provider.chat() call.

    All strategies must expose ``name`` (a stable identifier matching the
    config key, e.g. ``"afm"``) and implement ``pack``. Optional capabilities
    (summarizer wiring, per-message importance overrides) may be no-ops for
    strategies that don't track that state.
    """

    name: str

    def pack(
        self,
        messages: list[dict],
        current_query: str,
        plan_start_index: int | None = None,
        *,
        system_prompt_size: int = 0,
    ) -> list[dict]:
        """Return a budget-constrained message list. Must not mutate ``messages``.

        Args:
            system_prompt_size: token estimate of the system prompt that will
                be sent alongside these messages. AFM subtracts this from its
                budget so the total LLM call stays under the cap; other
                strategies may ignore it.
        """
        ...

    # ── Optional capabilities — strategies may no-op these ──────────

    def set_summarizer(self, provider) -> None:
        """Hook for strategies that compress via an LLM. Optional."""
        ...

    def set_importance(self, message_index: int, importance: Importance) -> None:
        """Hook for strategies that rank messages by importance. Optional."""
        ...

    def get_importance(self, message_index: int) -> Importance | None:
        """Return the importance assigned to a message, or None when unsupported."""
        ...
