"""Compression helpers for context manager.

Produces FULL/COMPRESSED/PLACEHOLDER variants of messages.
Holds the LLM summarizer and summary cache for context_manager.
"""
from __future__ import annotations

from runtime.schema import ScoredMessage, FidelityLevel
from runtime.context.scoring import message_text, estimate_tokens
from logger import get_logger

logger = get_logger(__name__)


def compress_message(msg: dict, index: int, *, max_chars: int, summarizer, summary_cache: dict) -> dict:
    """Produce a compressed version of a message."""
    from runtime import compressor
    role = msg["role"]
    content = msg["content"]

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
                compressed = compress_tool_result(original, max_chars, summarizer=summarizer, summary_cache=summary_cache)
                compressed_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": block["tool_use_id"],
                    "content": compressed,
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


def compress_tool_result(content: str, max_chars: int, *, summarizer, summary_cache: dict) -> str:
    """Compress a tool result. Uses LLM summarization if available and content is large enough."""
    from runtime import compressor
    # If content is small, use mechanical compression
    if len(content) <= max_chars * 2 or summarizer is None:
        return compressor.compress_tool_result(content, max_chars)

    # Check cache
    cache_key = content[:200]
    if cache_key in summary_cache:
        return summary_cache[cache_key]

    # LLM summarization
    try:
        from messenger import Messenger
        from providers.base import TextBlock
        messenger = Messenger()
        messenger.add_user_message(
            f"Summarize this tool output in under {max_chars} characters, "
            f"preserving key facts, values, and any errors:\n\n{content[:2000]}"
        )
        response = summarizer.chat(
            messages=messenger.get_messages(),
            tools=[],
            system="You are a concise summarizer. Return ONLY the summary, nothing else.",
            label="ContextManager",
        )
        summary = next(
            (b.text for b in response.content if isinstance(b, TextBlock)), ""
        )
        if summary and len(summary) <= max_chars * 1.5:
            summary_cache[cache_key] = summary
            return summary
    except Exception as e:
        logger.debug(f"  context_manager: LLM summarization failed — {e}")

    # Fallback to mechanical compression
    from runtime import compressor
    return compressor.compress_tool_result(content, max_chars)


def placeholder_message(msg: dict, index: int) -> dict:
    """Produce a placeholder stub for a message."""
    from runtime import compressor
    role = msg["role"]
    content = msg["content"]

    if role == "user" and isinstance(content, str):
        return {"role": "user", "content": compressor.placeholder_user(content, index)}

    if role == "user" and isinstance(content, list):
        # Tool results → single stub; must still be valid tool_result format for the API
        total_chars = sum(
            len(block.get("content", ""))
            for block in content
            if block.get("type") == "tool_result"
        )
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
        text = message_text(msg)
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
