"""Gemini provider.

Uses `google-genai` SDK (verified byte-faithful — see
_design/0002-foundation-phase1-gemini-sdk-byte-fidelity.md).

Translates between our provider-agnostic types (Message, ToolSpec, ContentBlock,
LLMResponse) and Gemini's native types. Captures raw response dict for replay.

Retry policy is in this layer (not the runtime layer) because retries are
provider-specific (e.g., rate-limit responses, transient 5xx). Config knobs
come from `config.provider.retry`.
"""
from __future__ import annotations

import os
import time
from typing import Any

from arc.config import ProviderConfig
from arc.runtime.hooks import ContentBlock, LLMRequest, LLMResponse, Message, ToolSpec


# Map our universal "role" strings to Gemini's role values.
# Gemini uses "user" and "model"; tool messages are embedded as parts.
_ROLE_TO_GEMINI = {
    "user": "user",
    "assistant": "model",
    "tool": "user",  # tool results come back as "user" with function_response part
}


class GeminiProvider:
    """Gemini implementation of LLMProvider."""

    name = "gemini"

    def __init__(self, cfg: ProviderConfig) -> None:
        from google import genai

        api_key = os.environ.get(cfg.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Gemini provider: env var {cfg.api_key_env!r} is not set\n"
                f"  set it in your .env or environment before running arc"
            )

        self._cfg = cfg
        self._client = genai.Client(api_key=api_key)

    # ── Public entry point ─────────────────────────────────────────────────

    def chat(self, req: LLMRequest) -> LLMResponse:
        """Send a request, retry per policy, return a translated response."""
        from google import genai
        from google.genai import types

        contents = self._messages_to_contents(req.messages)
        gemini_tools = self._tools_to_gemini(req.tools) if req.tools else None

        # Build the generation config from our params + Gemini-specific fields
        gen_config = types.GenerateContentConfig(
            system_instruction=req.system or None,
            temperature=req.params.get("temperature"),
            max_output_tokens=req.params.get("max_tokens"),
            top_p=req.params.get("top_p"),
            tools=gemini_tools,
        )

        resp = self._call_with_retry(req.model, contents, gen_config)

        return self._response_to_llm_response(resp)

    # ── Retry loop ─────────────────────────────────────────────────────────

    def _call_with_retry(self, model: str, contents: Any, config: Any) -> Any:
        """Exponential backoff, capped by config.provider.retry.

        Retries on any exception other than auth/quota-permanent errors.
        We deliberately keep the classification simple: retry everything up
        to the limit. Permanent errors will fail after `max_attempts` with
        a clear message.
        """
        cfg = self._cfg.retry
        backoff = cfg.backoff_base_seconds
        last_exc: Exception | None = None

        for attempt in range(1, cfg.max_attempts + 1):
            try:
                return self._client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                last_exc = exc
                if attempt >= cfg.max_attempts:
                    break
                time.sleep(min(backoff, cfg.backoff_max_seconds))
                backoff *= 2

        raise RuntimeError(
            f"Gemini call failed after {cfg.max_attempts} attempts: {last_exc}"
        ) from last_exc

    # ── Request translation: our types → Gemini types ──────────────────────

    def _messages_to_contents(self, messages: list[Message]) -> list[Any]:
        """Convert our Message list to Gemini's `contents` shape.

        Gemini's content is a list of `{role, parts: [{text|function_call|function_response}]}`.
        We pass dicts (Gemini SDK accepts them) rather than constructing types —
        less code, same wire format.
        """
        out: list[dict[str, Any]] = []
        for msg in messages:
            role = _ROLE_TO_GEMINI.get(msg.role, "user")

            # Simple text message
            if isinstance(msg.content, str):
                out.append({"role": role, "parts": [{"text": msg.content}]})
                continue

            # Content is a list of ContentBlocks (tool calls, results, mixed)
            parts: list[dict[str, Any]] = []
            for block in msg.content:
                if isinstance(block, ContentBlock):
                    if block.type == "text" and block.text is not None:
                        parts.append({"text": block.text})
                    elif block.type == "tool_use":
                        part_dict: dict[str, Any] = {
                            "function_call": {
                                "name": block.tool_name,
                                "args": block.tool_input or {},
                            }
                        }
                        # Echo back thought_signature (required by Gemini 3+)
                        if block.metadata and "thought_signature" in block.metadata:
                            part_dict["thought_signature"] = block.metadata["thought_signature"]
                        parts.append(part_dict)
                elif isinstance(block, dict):
                    # Raw passthrough for tool results: {"function_response": {...}}
                    parts.append(block)

            if parts:
                out.append({"role": role, "parts": parts})

        return out

    def _tools_to_gemini(self, tools: list[ToolSpec]) -> list[Any]:
        """Convert our ToolSpecs to Gemini Tool(function_declarations=...)."""
        from google.genai import types

        decls = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=t.input_schema,
            )
            for t in tools
        ]
        return [types.Tool(function_declarations=decls)]

    # ── Response translation: Gemini types → our types ────────────────────

    def _response_to_llm_response(self, resp: Any) -> LLMResponse:
        """Convert a GenerateContentResponse to our LLMResponse.

        The raw dict (resp.model_dump(mode='json')) is preserved verbatim in
        `.raw` for replay byte-fidelity.
        """
        # Extract the first candidate (Gemini may return multiple)
        candidate = resp.candidates[0] if resp.candidates else None
        stop_reason = self._translate_stop_reason(candidate)

        blocks: list[ContentBlock] = []
        if candidate and candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if getattr(part, "text", None) is not None:
                    blocks.append(ContentBlock(type="text", text=part.text))
                elif getattr(part, "function_call", None) is not None:
                    fc = part.function_call
                    # Gemini 3+ requires the thought_signature from each
                    # function_call to be echoed back on the next turn or
                    # the API returns 400 INVALID_ARGUMENT. Capture it in
                    # metadata so _messages_to_contents can re-emit it.
                    metadata: dict[str, Any] = {}
                    ts = getattr(part, "thought_signature", None)
                    if ts is not None:
                        metadata["thought_signature"] = ts
                    blocks.append(ContentBlock(
                        type="tool_use",
                        tool_use_id=fc.id if hasattr(fc, "id") and fc.id else fc.name,
                        tool_name=fc.name,
                        tool_input=dict(fc.args) if fc.args else {},
                        metadata=metadata or None,
                    ))

        usage = resp.usage_metadata
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0

        return LLMResponse(
            content=blocks,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw=resp.model_dump(mode="json"),
        )

    def _translate_stop_reason(self, candidate: Any) -> str:
        """Map Gemini's finish_reason enum to our string set.

        Our taxonomy: "end_turn" | "tool_use" | "max_tokens" | "other".
        Gemini's enum values include STOP, MAX_TOKENS, SAFETY, RECITATION,
        FUNCTION_CALL, OTHER.
        """
        if not candidate:
            return "other"
        fr = getattr(candidate, "finish_reason", None)
        if fr is None:
            return "other"

        # finish_reason may be an enum or a string depending on SDK version
        fr_str = fr.value if hasattr(fr, "value") else str(fr)
        fr_str = fr_str.upper().replace("FINISHREASON.", "")

        # Was there a function call in the parts? That's "tool_use" regardless
        if candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if getattr(part, "function_call", None) is not None:
                    return "tool_use"

        if fr_str == "STOP":
            return "end_turn"
        if fr_str == "MAX_TOKENS":
            return "max_tokens"
        return "other"
