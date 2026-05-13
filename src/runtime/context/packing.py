"""Chronological budget packing for the context manager.

pack_chronological() packs messages under the token budget while preserving
tool_use/tool_result pair atomicity.
"""
from __future__ import annotations

from runtime.schema import FidelityLevel, ScoredMessage
from runtime.context.scoring import message_text, estimate_tokens
from logger import get_logger

logger = get_logger(__name__)


def _try_fit(
    s: ScoredMessage,
    budget: int,
    *,
    max_chars: int,
    summarizer,
    summary_cache: dict,
) -> tuple[dict | None, int, FidelityLevel]:
    """Try to fit a message at its intended fidelity, downgrading as needed.

    Returns (packed_message, token_cost, fidelity) or (None, 0, ...) if it can't fit.
    """
    from runtime.context.compression import compress_message, placeholder_message

    msg = s.message
    text = message_text(msg)

    if s.fidelity == FidelityLevel.FULL:
        tokens = estimate_tokens(text)
        if tokens <= budget:
            return msg, tokens, FidelityLevel.FULL

    # Try compressed
    if s.fidelity in (FidelityLevel.FULL, FidelityLevel.COMPRESSED):
        compressed_msg = compress_message(msg, s.index, max_chars=max_chars, summarizer=summarizer, summary_cache=summary_cache)
        compressed_text = message_text(compressed_msg)
        tokens = estimate_tokens(compressed_text)
        if tokens <= budget:
            return compressed_msg, tokens, FidelityLevel.COMPRESSED

    # Try placeholder
    stub_msg = placeholder_message(msg, s.index)
    stub_text = message_text(stub_msg)
    tokens = estimate_tokens(stub_text)
    if tokens <= budget:
        return stub_msg, tokens, FidelityLevel.PLACEHOLDER

    return None, 0, s.fidelity


def pack_chronological(
    scored: list[ScoredMessage],
    *,
    budget: int,
    max_chars: int,
    summarizer,
    summary_cache: dict,
) -> list[ScoredMessage]:
    """Pack messages chronologically under the token budget.

    Tries intended fidelity first, downgrades if over budget.

    IMPORTANT: tool_use/tool_result pairs are treated as atomic units.
    Anthropic's API requires every tool_use block in an assistant message
    to have a matching tool_result in the immediately following user message.
    If we drop or downgrade one half, we must do the same to its partner.
    """
    # First pass: identify tool_use/tool_result pairs by index.
    pair_links = {}  # index -> partner_index
    for idx, s in enumerate(scored):
        msg = s.message
        if msg["role"] == "assistant" and isinstance(msg["content"], list):
            has_tool_use = any(b.get("type") == "tool_use" for b in msg["content"])
            if has_tool_use and idx + 1 < len(scored):
                partner = scored[idx + 1]
                if partner.message["role"] == "user" and isinstance(partner.message["content"], list):
                    has_tool_result = any(
                        b.get("type") == "tool_result" for b in partner.message["content"]
                    )
                    if has_tool_result:
                        pair_links[idx] = idx + 1
                        pair_links[idx + 1] = idx

    # Enforce: paired messages must share the same (minimum) fidelity
    fidelity_order = {FidelityLevel.FULL: 2, FidelityLevel.COMPRESSED: 1, FidelityLevel.PLACEHOLDER: 0}
    for idx, partner_idx in pair_links.items():
        if idx < partner_idx:  # process each pair once
            a, b = scored[idx], scored[partner_idx]
            min_fidelity = min(a.fidelity, b.fidelity, key=lambda f: fidelity_order[f])
            a.fidelity = min_fidelity
            b.fidelity = min_fidelity

    # Second pass: pack under budget, enforcing pair atomicity
    budget_left = budget
    result = []
    dropped = set()

    fit_kwargs = dict(max_chars=max_chars, summarizer=summarizer, summary_cache=summary_cache)

    for s in scored:
        if s.index in dropped:
            continue

        packed_msg, tokens, fidelity = _try_fit(s, budget_left, **fit_kwargs)

        if packed_msg is not None:
            if s.index in pair_links:
                partner_idx = pair_links[s.index]
                partner = scored[partner_idx]
                if partner_idx > s.index:
                    # Partner comes later — try to fit it too
                    partner_msg, partner_tokens, partner_fidelity = _try_fit(
                        partner, budget_left - tokens, **fit_kwargs
                    )
                    if partner_msg is not None:
                        s.message = packed_msg
                        s.token_estimate = tokens
                        s.fidelity = fidelity
                        budget_left -= tokens
                        result.append(s)

                        partner.message = partner_msg
                        partner.token_estimate = partner_tokens
                        partner.fidelity = partner_fidelity
                        budget_left -= partner_tokens
                        result.append(partner)
                        dropped.add(partner_idx)  # already processed
                    else:
                        # Can't fit pair — drop both
                        logger.debug(f"  context_manager: dropped pair at {s.index},{partner_idx}")
                        dropped.add(partner_idx)
                else:
                    # Partner was already processed (came before us)
                    s.message = packed_msg
                    s.token_estimate = tokens
                    s.fidelity = fidelity
                    budget_left -= tokens
                    result.append(s)
            else:
                # No partner — pack normally
                s.message = packed_msg
                s.token_estimate = tokens
                s.fidelity = fidelity
                budget_left -= tokens
                result.append(s)
        else:
            # Can't fit at all — drop (and partner if exists)
            if s.index in pair_links:
                dropped.add(pair_links[s.index])
            logger.debug(f"  context_manager: dropped message at index {s.index}")

    return result
