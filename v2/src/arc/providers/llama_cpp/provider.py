"""LlamaCppProvider — dispatches between `compat` (OpenAI shim) and
`grammar` (native /completion + GBNF) modes.

See _design/0015-llama-cpp-provider.md.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from arc.config import ProviderConfig
from arc.providers.openai_compat import (
    CompatCapabilities,
    OpenAICompatProvider,
    init_from_provider_config,
)
from arc.providers.llama_cpp import grammar as _grammar_mod
from arc.providers.llama_cpp.native_client import get_health, post_completion
from arc.runtime.hooks import ContentBlock, LLMRequest, LLMResponse, Message, ToolSpec

log = logging.getLogger("arc.providers.llama_cpp")


DEFAULT_BASE_URL = "http://localhost:8080/v1"


class LlamaCppProvider:
    """Dispatcher.  Owns the mode switch and the preflight log."""

    name = "llama_cpp"

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        mode = (cfg.params or {}).get("mode", "compat")

        if mode == "compat":
            kwargs = init_from_provider_config(
                cfg,
                default_base_url=DEFAULT_BASE_URL,
                default_api_key_env_value="sk-no-key",
                # llama-server's compat mode mishandles parallel calls across
                # most chat templates — off by default; users can opt in via
                # provider.params if their template handles it.
                capabilities=CompatCapabilities(
                    tool_use=True,
                    parallel_tool_calls=False,
                    json_mode=True,
                    json_schema=False,
                ),
            )
            self._impl: _ModeImpl = _CompatImpl(kwargs)
        elif mode == "grammar":
            self._impl = _GrammarImpl(cfg)
        else:
            raise ValueError(
                f"llama_cpp.params.mode must be 'compat' or 'grammar', got {mode!r}"
            )

        _preflight(cfg.base_url or DEFAULT_BASE_URL)

    def chat(self, req: LLMRequest) -> LLMResponse:
        return self._impl.chat(req)


# ── Mode implementations ───────────────────────────────────────────────────


class _ModeImpl:
    def chat(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover - interface
        raise NotImplementedError


class _CompatImpl(_ModeImpl):
    """Thin wrapper so the dispatcher can hold either impl by the same type."""

    def __init__(self, kwargs: dict[str, Any]) -> None:
        self._backend = OpenAICompatProvider(**kwargs)
        self._backend.name = "llama_cpp"

    def chat(self, req: LLMRequest) -> LLMResponse:
        return self._backend.chat(req)


class _GrammarImpl(_ModeImpl):
    """Native /completion path with a GBNF grammar generated per-request.

    The model is forced to emit exactly one of:
        ANSWER:\\n<text>
        TOOL:\\n{"name": "<tool>", "input": {...}}
    so parsing post-hoc never has to fall back / defend against garbage.
    """

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self._base_url = cfg.base_url or DEFAULT_BASE_URL

    def chat(self, req: LLMRequest) -> LLMResponse:
        grammar_text = _grammar_mod.compile_grammar(req.tools)
        prompt = _build_grammar_prompt(req.system, req.messages, req.tools)

        payload: dict[str, Any] = {
            "prompt": prompt,
            "grammar": grammar_text,
            "n_predict": int(req.params.get("max_tokens", 1024)),
            "temperature": float(req.params.get("temperature", 0.0)),
        }
        # Pass through llama.cpp native sampler knobs untouched.
        for key, value in (req.params or {}).items():
            if key in ("mode", "max_tokens", "temperature"):
                continue
            payload[key] = value

        body = _call_with_retry(self._cfg, self._base_url, payload, self._cfg.timeout_seconds)

        return _grammar_response_to_llm_response(body, grammar_text)


def _call_with_retry(
    cfg: ProviderConfig,
    base_url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    backoff = cfg.retry.backoff_base_seconds
    last_exc: Exception | None = None

    for attempt in range(1, cfg.retry.max_attempts + 1):
        try:
            return post_completion(
                base_url=base_url,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            last_exc = exc
            if attempt >= cfg.retry.max_attempts:
                break
            time.sleep(min(backoff, cfg.retry.backoff_max_seconds))
            backoff *= 2

    raise RuntimeError(
        f"llama_cpp grammar mode call failed after {cfg.retry.max_attempts} attempts: {last_exc}"
    ) from last_exc


# ── Prompt + response shaping ──────────────────────────────────────────────


def _build_grammar_prompt(
    system: str,
    messages: list[Message],
    tools: list[ToolSpec],
) -> str:
    """Flatten everything into a single prompt string for /completion.

    Format roughly:
        <system>
        <postamble: ANSWER:/TOOL: rules + tool list>

        User: ...
        Assistant: ...
        User: ...
        Assistant:

    The grammar constrains the model's output AFTER "Assistant:".  We don't
    apply any chat template here — `llama-server` is loaded with one, but
    /completion bypasses it.  This minimal format works with any
    instruction-tuned base.
    """
    parts: list[str] = []
    if system:
        parts.append(system.strip())

    parts.append(_postamble(tools))

    parts.append("")  # blank line between system block and conversation

    for msg in messages:
        if msg.role == "user":
            parts.append(f"User: {_render_user_or_tool(msg)}")
        elif msg.role == "assistant":
            parts.append(f"Assistant: {_render_assistant(msg)}")
        elif msg.role == "tool":
            parts.append(f"Tool result: {_render_user_or_tool(msg)}")

    # Trailing "Assistant:" cue — the grammar takes over from here.
    parts.append("Assistant:")
    return "\n".join(parts)


def _postamble(tools: list[ToolSpec]) -> str:
    """Instructions appended to the system prompt so the model knows the rules."""
    lines = [
        "",
        "Reply EXACTLY in ONE of these two formats (the server enforces this):",
        "",
        "  ANSWER:",
        "  <your reply to the user>",
        "",
        "  OR",
        "",
        '  TOOL:',
        '  {"name": "<tool-name>", "input": {<arguments-as-json>}}',
        "",
    ]
    if tools:
        lines.append("Available tools:")
        for t in tools:
            lines.append(f"- {t.name}: {t.description}")
    else:
        lines.append("(no tools available; you must use ANSWER:)")
    return "\n".join(lines)


def _render_user_or_tool(msg: Message) -> str:
    if isinstance(msg.content, str):
        return msg.content
    if isinstance(msg.content, list):
        for entry in msg.content:
            if isinstance(entry, dict) and "function_response" in entry:
                fr = entry["function_response"]
                return str(fr.get("response", {}).get("result", ""))
            if isinstance(entry, ContentBlock) and entry.type == "text":
                return entry.text or ""
            if isinstance(entry, str):
                return entry
    return ""


def _render_assistant(msg: Message) -> str:
    """Echo a prior assistant message back into the prompt in our grammar shape.

    Multi-turn grammar-mode conversations need to reconstruct prior assistant
    turns as either ANSWER: or TOOL: lines.
    """
    if isinstance(msg.content, str):
        return f"ANSWER:\n{msg.content}"
    if isinstance(msg.content, list):
        for b in msg.content:
            if isinstance(b, ContentBlock):
                if b.type == "tool_use":
                    payload = {"name": b.tool_name, "input": b.tool_input or {}}
                    return "TOOL:\n" + json.dumps(payload)
                if b.type == "text" and b.text:
                    return f"ANSWER:\n{b.text}"
    return "ANSWER:\n"


def _grammar_response_to_llm_response(body: dict[str, Any], grammar_text: str) -> LLMResponse:
    """Translate a /completion JSON body into our LLMResponse.

    `content` is grammar-constrained so it's either an ANSWER: or a TOOL:.
    No defensive parsing needed beyond a clean split — if it doesn't match,
    the grammar engine had a bug and a hard error here is fine.
    """
    content_text = body.get("content", "") or ""

    blocks: list[ContentBlock] = []
    stop_reason = "end_turn"

    stripped = content_text.lstrip()
    if stripped.startswith("ANSWER:"):
        # Strip the prefix + optional newline
        after = stripped[len("ANSWER:"):].lstrip("\n")
        blocks.append(ContentBlock(type="text", text=after.rstrip()))
        stop_reason = "end_turn"
    elif stripped.startswith("TOOL:"):
        after = stripped[len("TOOL:"):].lstrip("\n").strip()
        try:
            payload = json.loads(after)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"llama_cpp grammar mode: grammar produced text that didn't "
                f"parse as JSON after TOOL:\\n marker. raw={content_text!r}"
            ) from e
        from arc.runtime.ids import new_tool_call_id
        blocks.append(ContentBlock(
            type="tool_use",
            tool_use_id=new_tool_call_id(),
            tool_name=payload.get("name"),
            tool_input=dict(payload.get("input", {}) or {}),
        ))
        stop_reason = "tool_use"
    else:
        # Shouldn't happen — the grammar guarantees the prefix.  Treat as a
        # generic text answer so the runtime keeps moving instead of dying.
        blocks.append(ContentBlock(type="text", text=content_text.rstrip()))

    input_tokens = int(body.get("tokens_evaluated") or 0)
    output_tokens = int(body.get("tokens_predicted") or 0)

    timings = body.get("timings") or {}
    per_token_ms = timings.get("predicted_per_token_ms")

    # Augment raw with mode-specific telemetry the design promised.
    raw = dict(body)
    raw["_arc_llama_cpp"] = {
        "mode": "grammar",
        "grammar_size_bytes": len(grammar_text.encode("utf-8")),
    }
    if per_token_ms is not None:
        raw["_arc_llama_cpp"]["predicted_per_token_ms"] = per_token_ms

    return LLMResponse(
        content=blocks,
        stop_reason=stop_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw=raw,
    )


# ── Preflight ──────────────────────────────────────────────────────────────


def _preflight(base_url: str) -> None:
    body = get_health(base_url=base_url)
    if body is None:
        return
    status = body.get("status")
    if status == "ok":
        return
    if status == "loading model":
        log.info("llama_cpp: server is loading the model; the first call will block")
        return
    log.warning("llama_cpp: /health returned %r", body)


__all__ = ["LlamaCppProvider", "DEFAULT_BASE_URL"]
