"""Anthropic provider.

Uses the official `anthropic` SDK (verified byte-faithful — see
_design/0010-anthropic-provider.md and _tests/experiment_anthropic_sdk_fidelity.py).

Translates between our provider-agnostic types and Anthropic's native
types. Captures `resp.model_dump(mode="json")` into `LLMResponse.raw`
for replay byte-fidelity.

Key translation detail: Anthropic REQUIRES `tool_result` blocks to
reference a `tool_use_id` from the preceding assistant's `tool_use`
blocks. The universal Message type doesn't carry tool_call_id on
tool-role messages, so we match by POSITION against the most recent
assistant's tool_use blocks. Works for sequential AND parallel calls.
"""
from __future__ import annotations

import os
import time
from typing import Any

from arc.config import ProviderConfig
from arc.runtime.hooks import ContentBlock, LLMRequest, LLMResponse, Message, ToolSpec


class AnthropicProvider:
    """Anthropic implementation of LLMProvider."""

    name = "anthropic"

    def __init__(self, cfg: ProviderConfig) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as e:
            raise RuntimeError(
                "anthropic SDK not installed. Add `anthropic` to dependencies "
                "(pip install anthropic) before using the Anthropic provider."
            ) from e

        api_key = os.environ.get(cfg.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Anthropic provider: env var {cfg.api_key_env!r} is not set\n"
                f"  set it in your .env or environment before running arc"
            )

        self._cfg = cfg
        self._client = Anthropic(api_key=api_key)

    # ── Public entry point ─────────────────────────────────────────────

    def chat(self, req: LLMRequest) -> LLMResponse:
        messages = self._translate_messages(req.messages)
        tools = self._translate_tools(req.tools) if req.tools else None

        # Anthropic requires max_tokens, no default. Pick a sensible fallback
        # if the user didn't set one in provider.params.
        max_tokens = int(req.params.get("max_tokens", 4096))

        params: dict[str, Any] = {
            "model": req.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if req.system:
            params["system"] = req.system
        if tools:
            params["tools"] = tools

        # Pass-through optional params (don't pass max_tokens twice)
        for key in ("temperature", "top_p", "top_k", "stop_sequences"):
            if key in req.params:
                params[key] = req.params[key]

        resp = self._call_with_retry(params)
        return self._response_to_llm_response(resp)

    # ── Retry loop (mirrors GeminiProvider) ────────────────────────────

    def _call_with_retry(self, params: dict) -> Any:
        cfg = self._cfg.retry
        backoff = cfg.backoff_base_seconds
        last_exc: Exception | None = None

        for attempt in range(1, cfg.max_attempts + 1):
            try:
                return self._client.messages.create(**params)
            except Exception as exc:
                last_exc = exc
                if attempt >= cfg.max_attempts:
                    break
                time.sleep(min(backoff, cfg.backoff_max_seconds))
                backoff *= 2

        raise RuntimeError(
            f"Anthropic call failed after {cfg.max_attempts} attempts: {last_exc}"
        ) from last_exc

    # ── Request translation: ours → Anthropic ──────────────────────────

    def _translate_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Walk our message list, produce Anthropic's expected shape.

        Tool-role messages become user messages with `tool_result` blocks.
        Tool_use_ids are matched to the most recent assistant's tool_use
        blocks by position.
        """
        out: list[dict[str, Any]] = []
        pending_tool_ids: list[str] = []  # IDs from the most recent assistant
        pending_idx = 0                    # next index to consume

        for msg in messages:
            if msg.role == "user":
                out.append({
                    "role": "user",
                    "content": self._user_content(msg),
                })

            elif msg.role == "assistant":
                blocks, ids = self._assistant_blocks(msg)
                pending_tool_ids = ids
                pending_idx = 0
                out.append({"role": "assistant", "content": blocks})

            elif msg.role == "tool":
                # Anthropic: tool results are user messages with tool_result blocks
                tid = self._next_pending_id(pending_tool_ids, pending_idx)
                pending_idx += 1
                out.append({
                    "role": "user",
                    "content": [self._tool_result_block(msg, tid)],
                })

            # Skip unknown roles — never happens in current loop but defensive

        return out

    def _user_content(self, msg: Message) -> Any:
        """Anthropic accepts str or list-of-blocks for user content."""
        if isinstance(msg.content, str):
            return msg.content
        out_blocks: list[dict[str, Any]] = []
        for block in msg.content:
            if isinstance(block, ContentBlock):
                if block.type == "text" and block.text:
                    out_blocks.append({"type": "text", "text": block.text})
            elif isinstance(block, dict):
                # Raw passthrough (rare for user role)
                out_blocks.append(block)
        return out_blocks if out_blocks else str(msg.content)

    def _assistant_blocks(self, msg: Message) -> tuple[list[dict[str, Any]], list[str]]:
        """Returns (anthropic_content_blocks, tool_use_ids_in_order).

        The returned IDs let _translate_messages match subsequent tool-role
        messages to the right tool_use_id (Anthropic enforces this).
        """
        if isinstance(msg.content, str):
            return ([{"type": "text", "text": msg.content}], [])

        blocks: list[dict[str, Any]] = []
        ids: list[str] = []
        for b in msg.content:
            if not isinstance(b, ContentBlock):
                continue
            if b.type == "text" and b.text:
                blocks.append({"type": "text", "text": b.text})
            elif b.type == "thinking" and b.text:
                # Echo thinking blocks back — required when extended thinking
                # is in use across multi-turn tool conversations.
                td: dict[str, Any] = {"type": "thinking", "thinking": b.text}
                if b.metadata and "signature" in b.metadata:
                    td["signature"] = b.metadata["signature"]
                blocks.append(td)
            elif b.type == "tool_use":
                tid = b.tool_use_id or b.tool_name or "unknown"
                blocks.append({
                    "type": "tool_use",
                    "id": tid,
                    "name": b.tool_name or "",
                    "input": dict(b.tool_input or {}),
                })
                ids.append(tid)
        return (blocks, ids)

    def _tool_result_block(self, msg: Message, tool_use_id: str) -> dict[str, Any]:
        """Convert our universal tool message into Anthropic's tool_result block.

        Our loop appends tool messages with content shaped like:
            [{"function_response": {"name": ..., "response": {"result": "..."}}}]
        We extract the output and wrap in Anthropic's expected form.
        """
        output = ""
        if isinstance(msg.content, list) and msg.content:
            first = msg.content[0]
            if isinstance(first, dict) and "function_response" in first:
                fr = first["function_response"]
                resp = fr.get("response", {})
                output = resp.get("result", "")
            elif isinstance(first, str):
                output = first
        elif isinstance(msg.content, str):
            output = msg.content

        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": str(output),
        }

    def _next_pending_id(self, pending: list[str], idx: int) -> str:
        """Get the next tool_use_id to consume, or a fallback if we ran out.

        Running out means the model emitted more tool results than the
        previous assistant had tool_uses — shouldn't happen if the loop is
        well-formed. Use "unknown" so Anthropic returns a clear 400 we can
        debug, instead of silently sending bogus data.
        """
        if idx < len(pending):
            return pending[idx]
        return "unknown"

    def _translate_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        """Our ToolSpecs → Anthropic's tools list."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

    # ── Response translation: Anthropic → ours ────────────────────────

    def _response_to_llm_response(self, resp: Any) -> LLMResponse:
        blocks: list[ContentBlock] = []
        for b in resp.content:
            btype = getattr(b, "type", None)
            if btype == "text":
                blocks.append(ContentBlock(type="text", text=getattr(b, "text", "")))
            elif btype == "thinking":
                # Anthropic 3.7+/4+ extended thinking. Preserve the `signature`
                # field in metadata — Anthropic requires it echoed back on
                # subsequent turns when redacted_thinking is enabled.
                meta: dict[str, Any] = {}
                sig = getattr(b, "signature", None)
                if sig:
                    meta["signature"] = sig
                blocks.append(ContentBlock(
                    type="thinking",
                    text=getattr(b, "thinking", "") or getattr(b, "text", ""),
                    metadata=meta or None,
                ))
            elif btype == "tool_use":
                blocks.append(ContentBlock(
                    type="tool_use",
                    tool_use_id=getattr(b, "id", None),
                    tool_name=getattr(b, "name", None),
                    tool_input=dict(getattr(b, "input", {}) or {}),
                ))
            # Skip any other block types we haven't accounted for

        stop_reason = self._translate_stop_reason(getattr(resp, "stop_reason", None))

        usage = resp.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0

        return LLMResponse(
            content=blocks,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw=resp.model_dump(mode="json"),
        )

    def _translate_stop_reason(self, s: str | None) -> str:
        """Anthropic stop_reason → our universal taxonomy."""
        if s in ("end_turn", "tool_use", "max_tokens"):
            return s
        return "other"
