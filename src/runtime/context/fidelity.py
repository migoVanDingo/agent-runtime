"""Fidelity assignment for context manager scored messages."""
from __future__ import annotations

from runtime.schema import FidelityLevel, ScoredMessage


def assign_fidelity(
    scored: list[ScoredMessage],
    *,
    plan_start_index: int | None,
    threshold_high: float,
    threshold_mid: float,
) -> None:
    """Assign intended fidelity based on score thresholds. Mutates scored[*].fidelity in place.

    Messages produced during the current plan execution (index >=
    plan_start_index) get a minimum fidelity of COMPRESSED — never
    PLACEHOLDER. Semantic similarity can be low for intermediate results
    (e.g. a base64 string when the next step writes to a file), but the
    data is still required for correct execution.
    """
    for s in scored:
        if s.score >= threshold_high:
            s.fidelity = FidelityLevel.FULL
        elif s.score >= threshold_mid:
            s.fidelity = FidelityLevel.COMPRESSED
        else:
            s.fidelity = FidelityLevel.PLACEHOLDER

        # Enforce minimum fidelity for current-plan messages.
        # Tool results are working data the model needs to make progress —
        # stubbing them causes re-read loops. Protect them at FULL intent;
        # budget packing (_try_fit) will downgrade only if space is truly
        # exhausted. Other plan messages (user text, assistant turns) are
        # floored at COMPRESSED to prevent placeholder stubs.
        if plan_start_index is not None and s.index >= plan_start_index:
            msg = s.message
            is_tool_result = (
                msg["role"] == "user"
                and isinstance(msg.get("content"), list)
                and any(b.get("type") == "tool_result" for b in msg["content"])
            )
            if is_tool_result:
                if s.fidelity != FidelityLevel.FULL:
                    s.fidelity = FidelityLevel.FULL
            elif s.fidelity == FidelityLevel.PLACEHOLDER:
                s.fidelity = FidelityLevel.COMPRESSED
