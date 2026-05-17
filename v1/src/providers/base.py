"""Provider base types and abstract interface."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from providers.capabilities import ProviderCapabilities


_FINISH_REASON_MAP = {
    # Anthropic
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "stop_sequence",
    "refusal": "error",
    # OpenAI
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "error",
    # Gemini
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "error",
    "RECITATION": "error",
    "OTHER": "error",
}


def _normalize_finish_reason(stop_reason: str | None) -> str | None:
    if stop_reason is None:
        return None
    return _FINISH_REASON_MAP.get(stop_reason, stop_reason)


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_input_tokens: int | None = None       # tokens served from prefix cache
    cache_creation_tokens: int | None = None    # tokens used to create a new cache entry


@dataclass
class ProviderResponse:
    stop_reason: str
    content: list[TextBlock | ToolUseBlock] = field(default_factory=list)
    usage: TokenUsage | None = None


class BaseProvider(ABC):
    capabilities = ProviderCapabilities()

    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
        json_schema: dict | None = None,
        label: str = "",
    ) -> ProviderResponse:
        """Instrumented chat wrapper — emits llm.call.* events, delegates to _chat_impl."""
        from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
        from app_config import config as _cfg
        from runtime.cost import compute_cost

        identity = get_runtime_identity()
        bus = get_event_bus()
        provider_name = type(self).__name__
        model = getattr(self, "model", "unknown")
        temperature = getattr(_cfg.llm, "temperature", None) if _cfg else None
        max_tokens = getattr(_cfg.llm, "max_tokens", None) if _cfg else None

        started = RuntimeEvent(
            "llm.call.started",
            identity,
            payload={
                "label": label,
                "n_messages": len(messages),
                "n_tools": len(tools),
            },
            content={
                "system": system,
                "messages": messages,
                "tools": tools,
                "json_schema": json_schema,
            },
            stage=label or provider_name,
            provider=provider_name,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        bus.emit(started)

        t0 = time.monotonic()
        try:
            response = self._chat_impl(
                messages=messages, tools=tools, system=system,
                json_schema=json_schema, label=label,
            )
        except Exception as exc:
            bus.emit(RuntimeEvent(
                "llm.call.error",
                identity,
                payload={
                    "label": label,
                    "error": type(exc).__name__,
                    "error_message": str(exc)[:1000],
                },
                stage=label or provider_name,
                provider=provider_name,
                model=model,
                severity="error",
                parent_event_id=started.event_id,
            ))
            raise

        latency_ms = int((time.monotonic() - t0) * 1000)
        usage = response.usage
        input_tokens = usage.input_tokens if usage else None
        output_tokens = usage.output_tokens if usage else None
        cache_in = usage.cache_input_tokens if usage else None
        cache_create = usage.cache_creation_tokens if usage else None
        cost = compute_cost(model, input_tokens, output_tokens, cache_in, cache_create)
        finish_norm = _normalize_finish_reason(response.stop_reason)

        # Serialize content blocks to a JSON-safe representation.
        content_blocks: list[dict] = []
        for blk in response.content:
            if isinstance(blk, TextBlock):
                content_blocks.append({"type": "text", "text": blk.text})
            elif isinstance(blk, ToolUseBlock):
                content_blocks.append({
                    "type": "tool_use", "id": blk.id, "name": blk.name, "input": blk.input,
                })

        bus.emit(RuntimeEvent(
            "llm.call.completed",
            identity,
            payload={"label": label},
            content={"content_blocks": content_blocks},
            stage=label or provider_name,
            parent_event_id=started.event_id,
            provider=provider_name,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stop_reason=response.stop_reason,
            finish_reason_normalized=finish_norm,
            duration_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_input_tokens=cache_in,
            cache_creation_tokens=cache_create,
            cost_usd=cost,
        ))
        return response

    @abstractmethod
    def _chat_impl(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
        json_schema: dict | None = None,
        label: str = "",
    ) -> ProviderResponse:
        """Subclasses implement the actual provider call here."""
        ...
