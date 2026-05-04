"""Provider base types and abstract interface."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from providers.capabilities import ProviderCapabilities


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

        identity = get_runtime_identity()
        bus = get_event_bus()
        provider_name = type(self).__name__
        model = getattr(self, "model", "unknown")

        bus.emit(RuntimeEvent(
            "llm.call.started",
            identity,
            payload={
                "provider": provider_name,
                "model": model,
                "label": label,
                "n_messages": len(messages),
                "n_tools": len(tools),
            },
            stage=label or provider_name,
        ))

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
                    "provider": provider_name,
                    "model": model,
                    "label": label,
                    "error": type(exc).__name__,
                },
                stage=label or provider_name,
            ))
            raise

        latency_ms = int((time.monotonic() - t0) * 1000)
        bus.emit(RuntimeEvent(
            "llm.call.completed",
            identity,
            payload={
                "provider": provider_name,
                "model": model,
                "label": label,
                "stop_reason": response.stop_reason,
                "input_tokens": response.usage.input_tokens if response.usage else None,
                "output_tokens": response.usage.output_tokens if response.usage else None,
                "latency_ms": latency_ms,
            },
            stage=label or provider_name,
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
