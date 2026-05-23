"""OpenAI Chat Completions translation shim.

Shared base for any provider that speaks the OpenAI `/v1/chat/completions`
protocol — Ollama, llama.cpp's `llama-server` (compat mode), OpenAI itself
(when added), DeepSeek, Grok, etc.  Subclasses set `name`, `base_url`,
`api_key`, and a `CompatCapabilities` describing what the underlying
server actually supports.

Byte-fidelity: `.raw = response.model_dump(mode="json")`.  The OpenAI SDK
returns pydantic models, so `.model_dump()` produces a faithful JSON dict
that replay can reconstruct from without re-calling the API.

See _design/0014-ollama-provider.md.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from arc.config import ProviderConfig
from arc.runtime.hooks import ContentBlock, LLMRequest, LLMResponse, Message, ToolSpec


# ── Capabilities ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CompatCapabilities:
    """What an OpenAI-compatible server actually supports.

    Different backends (Ollama, llama.cpp, real OpenAI) speak the same wire
    protocol but disagree on which features work.  Subclasses declare what
    they can do; the shim raises a clear error at startup if the user has
    enabled something the backend can't honor (e.g., tool_use=False but
    tools in the config).
    """
    tool_use: bool = True
    parallel_tool_calls: bool = True
    json_mode: bool = True              # response_format={"type": "json_object"}
    json_schema: bool = False           # response_format={"type": "json_schema", ...}
    max_tokens_param: str = "max_tokens"  # OpenAI o-series uses "max_completion_tokens"


# ── Provider ───────────────────────────────────────────────────────────────


class OpenAICompatProvider:
    """Implements LLMProvider against any OpenAI-compatible Chat Completions API.

    Subclass to set defaults (base_url, capabilities) for a specific backend.
    The translation logic stays here.
    """

    # Subclasses override
    name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        retry,                          # RetryConfig
        params: dict[str, Any],
        capabilities: CompatCapabilities,
        timeout_seconds: float = 120.0,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                "openai SDK not installed. Add `openai` to dependencies "
                "(pip install openai) before using an OpenAI-compatible provider."
            ) from e

        self._base_url = base_url
        self._model = model
        self._retry = retry
        self._params = params
        self._caps = capabilities
        self._timeout = timeout_seconds
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_seconds)

    # ── Public entry point ─────────────────────────────────────────────

    def chat(self, req: LLMRequest) -> LLMResponse:
        if req.tools and not self._caps.tool_use:
            raise RuntimeError(
                f"provider {self.name!r} with model {req.model!r} doesn't support "
                f"tool calling, but {len(req.tools)} tool(s) were provided.\n"
                f"  pick a tool-capable model, or disable tools in your config."
            )

        messages = self._translate_messages(req.system, req.messages)
        tools = self._translate_tools(req.tools) if req.tools else None
        params = self._build_params(req, messages, tools)
        resp = self._call_with_retry(params)
        return self._response_to_llm_response(resp)

    # ── Params + retry ─────────────────────────────────────────────────

    def _build_params(
        self,
        req: LLMRequest,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": req.model or self._model,
            "messages": messages,
        }
        if tools:
            params["tools"] = tools
            if self._caps.parallel_tool_calls is False:
                params["parallel_tool_calls"] = False

        # max_tokens — keyed by capability (OpenAI o-series wants max_completion_tokens)
        if "max_tokens" in req.params:
            params[self._caps.max_tokens_param] = int(req.params["max_tokens"])

        # Pass-through scalars the OpenAI SDK accepts. Provider-specific knobs
        # (top_k, repeat_penalty, num_ctx, mirostat, ...) go through `extra_body`
        # so Ollama / llama-server see them.
        passthrough_known = ("temperature", "top_p", "stop", "seed", "presence_penalty",
                             "frequency_penalty", "logit_bias", "user", "response_format")
        extra_body: dict[str, Any] = {}
        for key, value in req.params.items():
            if key in ("max_tokens", "mode"):
                # max_tokens handled above; "mode" is a llama_cpp-only switch
                continue
            if key in passthrough_known:
                params[key] = value
            else:
                extra_body[key] = value
        if extra_body:
            params["extra_body"] = extra_body
        return params

    def _call_with_retry(self, params: dict[str, Any]) -> Any:
        cfg = self._retry
        backoff = cfg.backoff_base_seconds
        last_exc: Exception | None = None

        for attempt in range(1, cfg.max_attempts + 1):
            try:
                return self._client.chat.completions.create(**params)
            except Exception as exc:
                last_exc = exc
                if attempt >= cfg.max_attempts:
                    break
                time.sleep(min(backoff, cfg.backoff_max_seconds))
                backoff *= 2

        raise RuntimeError(
            f"{self.name} call failed after {cfg.max_attempts} attempts: {last_exc}"
        ) from last_exc

    # ── Translation: ours → OpenAI ─────────────────────────────────────

    def _translate_messages(
        self,
        system: str,
        messages: list[Message],
    ) -> list[dict[str, Any]]:
        """Flatten our Message list into OpenAI's `messages` array.

        Rules:
          - System prompt becomes the first message (role=system).
          - Assistant tool_use blocks become `tool_calls: [...]`.
          - Tool-role messages become `{"role": "tool", "tool_call_id": ...}`.
          - tool_call_id is matched by position from the previous assistant's
            tool_uses (same strategy AnthropicProvider uses).
        """
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})

        pending_tool_ids: list[str] = []
        pending_idx = 0

        for msg in messages:
            if msg.role == "user":
                out.append({"role": "user", "content": self._user_content(msg)})

            elif msg.role == "assistant":
                entry, ids = self._assistant_entry(msg)
                pending_tool_ids = ids
                pending_idx = 0
                out.append(entry)

            elif msg.role == "tool":
                tid = (
                    pending_tool_ids[pending_idx]
                    if pending_idx < len(pending_tool_ids)
                    else "unknown"
                )
                pending_idx += 1
                out.append({
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": self._tool_result_content(msg),
                })

        return out

    @staticmethod
    def _user_content(msg: Message) -> str:
        if isinstance(msg.content, str):
            return msg.content
        # ContentBlock list — flatten to text
        parts: list[str] = []
        for b in msg.content:
            if isinstance(b, ContentBlock) and b.type == "text" and b.text:
                parts.append(b.text)
        return "\n".join(parts)

    @staticmethod
    def _assistant_entry(msg: Message) -> tuple[dict[str, Any], list[str]]:
        if isinstance(msg.content, str):
            return ({"role": "assistant", "content": msg.content}, [])

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        ids: list[str] = []
        for b in msg.content:
            if not isinstance(b, ContentBlock):
                continue
            if b.type == "text" and b.text:
                text_parts.append(b.text)
            elif b.type == "tool_use":
                tid = b.tool_use_id or b.tool_name or "unknown"
                ids.append(tid)
                tool_calls.append({
                    "id": tid,
                    "type": "function",
                    "function": {
                        "name": b.tool_name or "",
                        "arguments": json.dumps(b.tool_input or {}),
                    },
                })
            # thinking blocks have no OpenAI equivalent — drop

        entry: dict[str, Any] = {"role": "assistant"}
        # OpenAI accepts content=None when there are tool_calls; non-null otherwise
        entry["content"] = "\n".join(text_parts) if text_parts else None
        if tool_calls:
            entry["tool_calls"] = tool_calls
        return (entry, ids)

    @staticmethod
    def _tool_result_content(msg: Message) -> str:
        """Pull the canonical string out of our universal tool message shape.

        The runtime's loop appends tool messages as:
            content=[{"function_response": {"name": ..., "response": {"result": "..."}}}]
        Match the Anthropic provider's extraction logic so all backends see
        the same string.
        """
        if isinstance(msg.content, list) and msg.content:
            first = msg.content[0]
            if isinstance(first, dict) and "function_response" in first:
                fr = first["function_response"]
                return str(fr.get("response", {}).get("result", ""))
            if isinstance(first, str):
                return first
        if isinstance(msg.content, str):
            return msg.content
        return ""

    @staticmethod
    def _translate_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    # ── Translation: OpenAI → ours ────────────────────────────────────

    def _response_to_llm_response(self, resp: Any) -> LLMResponse:
        # Single choice — multi-choice (`n>1`) is not in our protocol.
        choice = resp.choices[0] if getattr(resp, "choices", None) else None
        message = getattr(choice, "message", None) if choice else None

        blocks: list[ContentBlock] = []
        if message:
            text = getattr(message, "content", None)
            if text:
                blocks.append(ContentBlock(type="text", text=text))

            for tc in getattr(message, "tool_calls", None) or []:
                fn = getattr(tc, "function", None)
                if not fn:
                    continue
                raw_args = getattr(fn, "arguments", "") or ""
                if isinstance(raw_args, dict):
                    tool_input = raw_args
                else:
                    try:
                        tool_input = json.loads(raw_args) if raw_args.strip() else {}
                    except json.JSONDecodeError as e:
                        raise RuntimeError(
                            f"{self.name}: model emitted invalid JSON tool arguments "
                            f"for {getattr(fn, 'name', '?')!r}: {e}\n"
                            f"  raw: {raw_args!r}"
                        ) from e
                blocks.append(ContentBlock(
                    type="tool_use",
                    tool_use_id=getattr(tc, "id", None),
                    tool_name=getattr(fn, "name", None),
                    tool_input=tool_input,
                ))

        finish_reason = getattr(choice, "finish_reason", None) if choice else None
        stop_reason = self._translate_stop_reason(finish_reason)

        usage = getattr(resp, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0

        return LLMResponse(
            content=blocks,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw=_dump_response(resp),
        )

    @staticmethod
    def _translate_stop_reason(s: Any) -> str:
        if s == "tool_calls":
            return "tool_use"
        if s == "length":
            return "max_tokens"
        if s == "stop":
            return "end_turn"
        return "other"


# ── Helpers ────────────────────────────────────────────────────────────────


def _dump_response(resp: Any) -> dict[str, Any]:
    """Best-effort JSON dict of the SDK response, for `.raw`.

    Real OpenAI SDK responses are pydantic models with `model_dump(mode="json")`.
    Some compat servers return objects that don't subclass BaseModel; fall back
    to `dict()` or a manual shape.
    """
    if hasattr(resp, "model_dump"):
        try:
            return resp.model_dump(mode="json")
        except Exception:
            pass
    if isinstance(resp, dict):
        return resp
    try:
        return dict(resp)
    except Exception:
        return {"_repr": repr(resp)}


# ── Convenience for subclass constructors ─────────────────────────────────


def init_from_provider_config(
    cfg: ProviderConfig,
    *,
    default_base_url: str,
    default_api_key_env_value: str,
    capabilities: CompatCapabilities,
) -> dict[str, Any]:
    """Helper: turn a ProviderConfig + defaults into kwargs for the base ctor.

    Used by Ollama and llama.cpp shims so they don't repeat the boilerplate.
    """
    import os

    base_url = cfg.base_url or default_base_url
    api_key = os.environ.get(cfg.api_key_env, default_api_key_env_value)
    return dict(
        base_url=base_url,
        api_key=api_key,
        model=cfg.model,
        retry=cfg.retry,
        params=cfg.params,
        capabilities=capabilities,
        timeout_seconds=cfg.timeout_seconds,
    )
