"""Shared request/response translation between arc's types and google-genai's.

Used by both `gemini` (public API, key-based) and `vertex_gemini` (Vertex AI,
IAM-based) providers. The translation is identical — only client construction
differs. Keeping the logic in one place avoids drift between the two providers.

These are module-level functions, not methods, because they have no provider-
instance state. Both providers import them.
"""
from __future__ import annotations

import json
from typing import Any

from arc.runtime.hooks import ContentBlock, LLMResponse, Message, ToolSpec


# Map arc's universal "role" strings to Gemini's role values.
# Gemini uses "user" and "model"; tool messages are embedded as parts within
# a "user"-role message (function_response part).
ROLE_TO_GEMINI = {
    "user": "user",
    "assistant": "model",
    "tool": "user",
}


# ── Request translation ──────────────────────────────────────────────────


def messages_to_contents(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert arc Message list to Gemini's `contents` shape.

    Gemini's content is a list of `{role, parts: [{text|function_call|function_response}]}`.
    We pass dicts (Gemini SDK accepts them) rather than constructing types —
    less code, same wire format.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = ROLE_TO_GEMINI.get(msg.role, "user")

        if isinstance(msg.content, str):
            out.append({"role": role, "parts": [{"text": msg.content}]})
            continue

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
                    if block.metadata and "thought_signature" in block.metadata:
                        part_dict["thought_signature"] = block.metadata["thought_signature"]
                    parts.append(part_dict)
            elif isinstance(block, dict):
                parts.append(block)

        if parts:
            out.append({"role": role, "parts": parts})

    return out


# JSON Schema keywords Gemini's function-declaration schema rejects outright.
# `additionalProperties` (free-form dicts) has no Gemini equivalent; the draft
# metadata keys are noise. Unions (`anyOf`/`oneOf`) are flattened separately.
_GEMINI_DROP_KEYS = ("additionalProperties", "additional_properties", "$schema")
_UNION_KEYS = ("anyOf", "any_of", "oneOf", "one_of")


def sanitize_gemini_schema(node: Any) -> Any:
    """Rewrite a JSON Schema into the subset Gemini function-calling accepts.

    MCP tools (via FastMCP/pydantic) emit `anyOf: [X, {type: null}]` for
    optional fields and `additionalProperties` for dicts — both 400 the Gemini
    API. This collapses nullable unions to `type + nullable` and strips the
    unsupported keys, recursively.
    """
    if isinstance(node, list):
        return [sanitize_gemini_schema(x) for x in node]
    if not isinstance(node, dict):
        return node

    node = dict(node)

    for uk in _UNION_KEYS:
        if uk not in node:
            continue
        options = node.pop(uk) or []
        nullable = any(isinstance(o, dict) and o.get("type") == "null" for o in options)
        non_null = [o for o in options if not (isinstance(o, dict) and o.get("type") == "null")]
        chosen = non_null[0] if non_null else {}  # best-effort on multi-type unions
        node = {**node, **chosen}  # chosen's type/items/etc. win
        if nullable:
            node["nullable"] = True

    for bad in _GEMINI_DROP_KEYS:
        node.pop(bad, None)

    if isinstance(node.get("properties"), dict):
        node["properties"] = {k: sanitize_gemini_schema(v) for k, v in node["properties"].items()}
    if "items" in node:
        node["items"] = sanitize_gemini_schema(node["items"])

    return node


def tools_to_gemini(tools: list[ToolSpec]) -> list[Any]:
    """Convert arc ToolSpecs to Gemini Tool(function_declarations=...)."""
    from google.genai import types

    decls = [
        types.FunctionDeclaration(
            name=t.name,
            description=t.description,
            parameters=sanitize_gemini_schema(t.input_schema),
        )
        for t in tools
    ]
    return [types.Tool(function_declarations=decls)]


# ── Response translation ──────────────────────────────────────────────────


def response_to_llm_response(resp: Any) -> LLMResponse:
    """Convert a GenerateContentResponse to arc's LLMResponse.

    `.raw` carries the verbatim provider response dict for replay byte-fidelity.
    """
    candidate = resp.candidates[0] if resp.candidates else None
    stop_reason = translate_stop_reason(candidate)

    blocks: list[ContentBlock] = []
    if candidate and candidate.content and candidate.content.parts:
        for part in candidate.content.parts:
            if getattr(part, "text", None) is not None:
                blocks.append(ContentBlock(type="text", text=part.text))
            elif getattr(part, "function_call", None) is not None:
                fc = part.function_call
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


def translate_stop_reason(candidate: Any) -> str:
    """Map Gemini's finish_reason enum to arc's string set.

    arc taxonomy: "end_turn" | "tool_use" | "max_tokens" | "other".
    """
    if not candidate:
        return "other"
    fr = getattr(candidate, "finish_reason", None)
    if fr is None:
        return "other"

    fr_str = fr.value if hasattr(fr, "value") else str(fr)
    fr_str = fr_str.upper().replace("FINISHREASON.", "")

    if candidate.content and candidate.content.parts:
        for part in candidate.content.parts:
            if getattr(part, "function_call", None) is not None:
                return "tool_use"

    if fr_str == "STOP":
        return "end_turn"
    if fr_str == "MAX_TOKENS":
        return "max_tokens"
    return "other"


# ── Vertex-only: auto-attach gs:// URIs from tool results ────────────────


def find_auto_attach_file(messages: list[Message]) -> tuple[str, str] | None:
    """Scan tool results in messages; return (uri, mime_type) for the most
    recent one that looks like a video/image at a `gs://` URI.

    Used by `vertex_gemini` to auto-include the file as a `file_data` Part
    in the next request. The public `gemini` provider does NOT call this —
    it can't read gs:// URIs natively.

    Returns None when no matching tool result is present.
    """
    found: tuple[str, str] | None = None
    for msg in messages:
        if msg.role != "tool":
            continue
        # Tool messages from the runtime loop carry content as a list of
        # `{"function_response": {"name": ..., "response": {"result": str}}}`.
        if not isinstance(msg.content, list):
            continue
        for block in msg.content:
            if not isinstance(block, dict):
                continue
            fr = block.get("function_response")
            if not isinstance(fr, dict):
                continue
            response = fr.get("response")
            if not isinstance(response, dict):
                continue
            raw_result = response.get("result")
            if not isinstance(raw_result, str):
                continue
            try:
                parsed = json.loads(raw_result)
            except (ValueError, TypeError):
                continue
            if not isinstance(parsed, dict):
                continue
            uri = parsed.get("uri")
            content_type = parsed.get("content_type")
            if not isinstance(uri, str) or not isinstance(content_type, str):
                continue
            if not uri.startswith("gs://"):
                continue
            base_ct = content_type.split(";", 1)[0].strip().lower()
            if not (base_ct.startswith("video/") or base_ct.startswith("image/")
                    or base_ct.startswith("audio/")):
                continue
            # Keep iterating — we want the LAST matching tool result, not the first.
            found = (uri, base_ct)
    return found


def append_file_data_to_last_user_message(
    contents: list[dict[str, Any]],
    uri: str,
    mime_type: str,
) -> None:
    """Append a `file_data` part to the LAST `user`-role message in `contents`.

    Used by `vertex_gemini` after detecting an auto-attachable file. The
    Gemini API requires file_data to live in a user message (the tool result
    message is also "user" per ROLE_TO_GEMINI).
    """
    for msg in reversed(contents):
        if msg.get("role") == "user":
            parts = msg.setdefault("parts", [])
            parts.append({"file_data": {"file_uri": uri, "mime_type": mime_type}})
            return
    # No user message to attach to — append a synthetic one. Rare edge case;
    # happens only if the parent dispatched with no task/messages.
    contents.append({"role": "user", "parts": [
        {"file_data": {"file_uri": uri, "mime_type": mime_type}},
    ]})
