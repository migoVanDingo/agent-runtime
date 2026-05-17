"""Message scoring functions for the context manager.

Computes per-message scores from semantic similarity, recency decay,
and rule-based importance classification.
"""
from __future__ import annotations

import math

from runtime.schema import FidelityLevel, Importance, ScoredMessage
from logger import get_logger

logger = get_logger(__name__)


def estimate_tokens(text: str) -> int:
    """Rough token estimate: chars / 4."""
    return max(1, len(text) // 4)


def message_text(msg: dict) -> str:
    """Extract displayable text from a message for embedding/sizing."""
    from runtime import compressor
    content = msg["content"]
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                if name == "write_file":
                    parts.append(compressor.summarize_write_file(inp))
                else:
                    parts.append(f"[tool_use: {name}]")
            elif block.get("type") == "tool_result":
                parts.append(block.get("content", ""))
        return " ".join(parts)
    return str(content)


def classify_importance(
    msg: dict,
    index: int,
    total: int,
    overrides: dict[int, Importance],
) -> Importance:
    """Importance classification. Checks LLM overrides first, then rule-based."""
    if index in overrides:
        return overrides[index]

    role = msg["role"]
    content = msg["content"]

    # First user message is always critical (task definition)
    if role == "user" and index == 0 and isinstance(content, str):
        return Importance.CRITICAL

    # User text messages are generally high importance
    if role == "user" and isinstance(content, str):
        return Importance.HIGH

    # Tool results
    if role == "user" and isinstance(content, list):
        total_chars = sum(len(b.get("content", "")) for b in content)
        if total_chars > 500:
            return Importance.LOW  # large tool outputs are primary bloat
        return Importance.MEDIUM

    # Assistant messages
    if role == "assistant" and isinstance(content, list):
        has_write = any(
            b.get("type") == "tool_use" and b.get("name") == "write_file"
            for b in content
        )
        if has_write:
            return Importance.LOW  # write_file content is redundant
        has_tool_use = any(b.get("type") == "tool_use" for b in content)
        if has_tool_use:
            return Importance.MEDIUM
        return Importance.MEDIUM

    return Importance.MEDIUM


def score_messages(
    messages: list[dict],
    current_query: str,
    plan_start_index: int | None,
    *,
    embedding_model,
    importance_overrides: dict[int, Importance],
    half_life: int,
) -> list[ScoredMessage]:
    """Score each message on similarity, recency, and importance."""
    n = len(messages)

    if embedding_model is not None and current_query:
        query_emb = embedding_model.encode(current_query, show_progress_bar=False)
    else:
        query_emb = None

    scored = []
    for i, msg in enumerate(messages):
        importance = classify_importance(msg, i, n, importance_overrides)

        # Boost messages from current plan execution
        if plan_start_index is not None and i >= plan_start_index:
            if importance in (Importance.LOW, Importance.MEDIUM):
                importance = Importance.HIGH

        # Critical messages always get max score
        if importance == Importance.CRITICAL:
            score = 1.0
        else:
            # Semantic similarity
            if query_emb is not None and embedding_model is not None:
                text = message_text(msg)[:500]  # cap embedding input
                msg_emb = embedding_model.encode(text, show_progress_bar=False)
                from sklearn.metrics.pairwise import cosine_similarity
                sim = float(cosine_similarity([query_emb], [msg_emb])[0][0])
                sim = max(0.0, sim)
            else:
                sim = 0.5  # neutral when no embedding model

            # Recency decay
            age = n - 1 - i  # 0 for most recent, n-1 for oldest
            recency = math.pow(0.5, age / half_life)

            # Weighted combination based on importance tier
            if importance == Importance.HIGH:
                score = sim * (0.4 + 0.4 * recency)
            elif importance == Importance.MEDIUM:
                score = sim * (0.3 + 0.3 * recency)
            else:  # LOW
                score = sim * (0.25 * recency)

        text = message_text(msg)
        scored.append(ScoredMessage(
            index=i,
            message=msg,
            score=score,
            importance=importance,
            fidelity=FidelityLevel.FULL,  # will be assigned in next step
            token_estimate=estimate_tokens(text),
        ))

    return scored
