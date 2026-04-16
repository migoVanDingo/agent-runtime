"""AFM-inspired non-destructive context manager.

The Messenger stores full history unchanged. Before each provider.chat() call,
the ContextManager produces a budget-constrained version by scoring each message
and assigning fidelity levels (FULL / COMPRESSED / PLACEHOLDER).

Scoring uses three signals:
  1. Semantic similarity to current query (embedding cosine)
  2. Recency decay (exponential half-life)
  3. Rule-based importance classification
"""

from __future__ import annotations
import json
import math
from runtime.schema import FidelityLevel, Importance, ScoredMessage
from runtime import compressor
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: chars / 4."""
    return max(1, len(text) // 4)


def _message_text(msg: dict) -> str:
    """Extract displayable text from a message for embedding/sizing."""
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


class ContextManager:

    def __init__(self, embedding_model=None):
        """
        Args:
            embedding_model: a SentenceTransformer instance (shared with router).
                             If None, similarity scoring is disabled — only recency
                             and importance are used.
        """
        self._model = embedding_model
        cfg = config.runtime.context_manager
        self._budget = cfg.message_budget_tokens
        self._half_life = cfg.half_life_turns
        self._threshold_high = cfg.threshold_high
        self._threshold_mid = cfg.threshold_mid
        self._compressed_max = cfg.compressed_max_chars

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

        # Quick check: if we're under budget, pass through
        total = sum(_estimate_tokens(_message_text(m)) for m in messages)
        if total <= self._budget:
            return messages

        logger.info(f"  context_manager: {total} tokens est. > budget {self._budget} — packing")

        scored = self._score_messages(messages, current_query, plan_start_index)
        self._assign_fidelity(scored)
        packed = self._pack_chronological(scored)

        packed_total = sum(s.token_estimate for s in packed)
        fidelity_counts = {}
        for s in packed:
            fidelity_counts[s.fidelity.value] = fidelity_counts.get(s.fidelity.value, 0) + 1
        logger.info(f"  context_manager: packed to {packed_total} tokens — {fidelity_counts}")

        return [s.message for s in packed]

    def _score_messages(self, messages: list[dict], current_query: str, plan_start_index: int | None = None) -> list[ScoredMessage]:
        """Score each message on similarity, recency, and importance."""
        n = len(messages)

        # Compute embeddings if model available
        if self._model is not None and current_query:
            query_emb = self._model.encode(current_query, show_progress_bar=False)
        else:
            query_emb = None

        scored = []
        for i, msg in enumerate(messages):
            importance = self._classify_importance(msg, i, n)

            # Boost messages from current plan execution
            if plan_start_index is not None and i >= plan_start_index:
                if importance == Importance.LOW:
                    importance = Importance.HIGH
                elif importance == Importance.MEDIUM:
                    importance = Importance.HIGH

            # Critical messages always get max score
            if importance == Importance.CRITICAL:
                score = 1.0
            else:
                # Semantic similarity
                if query_emb is not None and self._model is not None:
                    text = _message_text(msg)[:500]  # cap embedding input
                    msg_emb = self._model.encode(text, show_progress_bar=False)
                    from sklearn.metrics.pairwise import cosine_similarity
                    sim = float(cosine_similarity([query_emb], [msg_emb])[0][0])
                    sim = max(0.0, sim)
                else:
                    sim = 0.5  # neutral when no embedding model

                # Recency decay
                age = n - 1 - i  # 0 for most recent, n-1 for oldest
                recency = math.pow(0.5, age / self._half_life)

                # Weighted combination based on importance tier
                if importance == Importance.HIGH:
                    score = sim * (0.4 + 0.4 * recency)
                elif importance == Importance.MEDIUM:
                    score = sim * (0.3 + 0.3 * recency)
                else:  # LOW
                    score = sim * (0.25 * recency)

            text = _message_text(msg)
            scored.append(ScoredMessage(
                index=i,
                message=msg,
                score=score,
                importance=importance,
                fidelity=FidelityLevel.FULL,  # will be assigned in next step
                token_estimate=_estimate_tokens(text),
            ))

        return scored

    def _classify_importance(self, msg: dict, index: int, total: int) -> Importance:
        """Rule-based importance classification."""
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
            has_tool_use = any(b.get("type") == "tool_use" for b in content)
            has_write = any(
                b.get("type") == "tool_use" and b.get("name") == "write_file"
                for b in content
            )
            if has_write:
                return Importance.LOW  # write_file content is redundant
            if has_tool_use:
                return Importance.MEDIUM
            return Importance.MEDIUM

        return Importance.MEDIUM

    def _assign_fidelity(self, scored: list[ScoredMessage]) -> None:
        """Assign intended fidelity based on score thresholds."""
        for s in scored:
            if s.score >= self._threshold_high:
                s.fidelity = FidelityLevel.FULL
            elif s.score >= self._threshold_mid:
                s.fidelity = FidelityLevel.COMPRESSED
            else:
                s.fidelity = FidelityLevel.PLACEHOLDER

    def _pack_chronological(self, scored: list[ScoredMessage]) -> list[ScoredMessage]:
        """Pack messages chronologically under the token budget.

        Tries intended fidelity first, downgrades if over budget.

        IMPORTANT: tool_use/tool_result pairs are treated as atomic units.
        Anthropic's API requires every tool_use block in an assistant message
        to have a matching tool_result in the immediately following user message.
        If we drop or downgrade one half, we must do the same to its partner.
        """
        # First pass: identify tool_use/tool_result pairs by index.
        # An assistant msg at index i with tool_use blocks must be paired
        # with the user msg at index i+1 containing tool_results.
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
        for idx, partner_idx in pair_links.items():
            if idx < partner_idx:  # process each pair once
                a, b = scored[idx], scored[partner_idx]
                # Use the lower fidelity of the two
                fidelity_order = {FidelityLevel.FULL: 2, FidelityLevel.COMPRESSED: 1, FidelityLevel.PLACEHOLDER: 0}
                min_fidelity = min(a.fidelity, b.fidelity, key=lambda f: fidelity_order[f])
                a.fidelity = min_fidelity
                b.fidelity = min_fidelity

        # Second pass: pack under budget, enforcing pair atomicity
        budget_left = self._budget
        result = []
        dropped = set()

        for s in scored:
            if s.index in dropped:
                continue

            packed_msg, tokens, fidelity = self._try_fit(s, budget_left)

            if packed_msg is not None:
                # If this message has a partner, the partner must also fit
                if s.index in pair_links:
                    partner_idx = pair_links[s.index]
                    partner = scored[partner_idx]
                    if partner_idx > s.index:
                        # Partner comes later — try to fit it too
                        partner_msg, partner_tokens, partner_fidelity = self._try_fit(
                            partner, budget_left - tokens
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

    def _try_fit(self, s: ScoredMessage, budget: int) -> tuple[dict | None, int, FidelityLevel]:
        """Try to fit a message at its intended fidelity, downgrading as needed.

        Returns (packed_message, token_cost, fidelity) or (None, 0, ...) if it can't fit.
        """
        msg = s.message
        text = _message_text(msg)

        if s.fidelity == FidelityLevel.FULL:
            tokens = _estimate_tokens(text)
            if tokens <= budget:
                return msg, tokens, FidelityLevel.FULL

        # Try compressed
        if s.fidelity in (FidelityLevel.FULL, FidelityLevel.COMPRESSED):
            compressed_msg = self._compress_message(msg, s.index)
            compressed_text = _message_text(compressed_msg)
            tokens = _estimate_tokens(compressed_text)
            if tokens <= budget:
                return compressed_msg, tokens, FidelityLevel.COMPRESSED

        # Try placeholder
        stub_msg = self._placeholder_message(msg, s.index)
        stub_text = _message_text(stub_msg)
        tokens = _estimate_tokens(stub_text)
        if tokens <= budget:
            return stub_msg, tokens, FidelityLevel.PLACEHOLDER

        return None, 0, s.fidelity

    def _compress_message(self, msg: dict, index: int) -> dict:
        """Produce a compressed version of a message."""
        role = msg["role"]
        content = msg["content"]
        max_chars = self._compressed_max

        if role == "user" and isinstance(content, str):
            if len(content) <= max_chars:
                return msg
            return {"role": "user", "content": content[:max_chars] + "..."}

        if role == "user" and isinstance(content, list):
            # Tool results — compress each result
            compressed_blocks = []
            for block in content:
                if block.get("type") == "tool_result":
                    original = block.get("content", "")
                    compressed_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block["tool_use_id"],
                        "content": compressor.compress_tool_result(original, max_chars),
                    })
                else:
                    compressed_blocks.append(block)
            return {"role": "user", "content": compressed_blocks}

        if role == "assistant" and isinstance(content, list):
            compressed_blocks = []
            for block in content:
                if block.get("type") == "text":
                    compressed_blocks.append({
                        "type": "text",
                        "text": compressor.compress_assistant_text(block["text"], max_chars),
                    })
                elif block.get("type") == "tool_use" and block.get("name") == "write_file":
                    # Replace write_file with summary
                    compressed_blocks.append({
                        "type": "tool_use",
                        "id": block["id"],
                        "name": block["name"],
                        "input": {"path": block["input"].get("path", "?"),
                                  "content": compressor.summarize_write_file(block["input"])},
                    })
                else:
                    compressed_blocks.append(block)
            return {"role": "assistant", "content": compressed_blocks}

        return msg

    def _placeholder_message(self, msg: dict, index: int) -> dict:
        """Produce a placeholder stub for a message."""
        role = msg["role"]
        content = msg["content"]

        if role == "user" and isinstance(content, str):
            return {"role": "user", "content": compressor.placeholder_user(content, index)}

        if role == "user" and isinstance(content, list):
            # Tool results → single stub
            tool_names = []
            total_chars = 0
            for block in content:
                if block.get("type") == "tool_result":
                    total_chars += len(block.get("content", ""))
            stub = f"[tool results: {len(content)} result(s), {total_chars} chars total]"
            # Must still be valid tool_result format for the API
            stubbed_blocks = []
            for block in content:
                if block.get("type") == "tool_result":
                    stubbed_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block["tool_use_id"],
                        "content": f"[result: {len(block.get('content', ''))} chars]",
                    })
                else:
                    stubbed_blocks.append(block)
            return {"role": "user", "content": stubbed_blocks}

        if role == "assistant" and isinstance(content, list):
            text = _message_text(msg)
            stub_text = compressor.placeholder_assistant(text)
            # Preserve tool_use blocks as stubs (API requires matching IDs)
            stubbed_blocks = []
            for block in content:
                if block.get("type") == "text":
                    stubbed_blocks.append({"type": "text", "text": stub_text})
                elif block.get("type") == "tool_use":
                    stubbed_blocks.append({
                        "type": "tool_use",
                        "id": block["id"],
                        "name": block["name"],
                        "input": {},
                    })
            return {"role": "assistant", "content": stubbed_blocks}

        return msg
