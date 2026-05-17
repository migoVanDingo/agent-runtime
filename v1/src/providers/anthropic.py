"""Anthropic provider.

Supports structured JSON output via the single-tool trick: when a caller
passes `json_schema`, we declare a synthetic `respond` tool whose input_schema
matches the desired output, and force `tool_choice={"type": "tool", "name": "respond"}`.
The model's tool-use input IS the structured response.
"""
import json
import time
import anthropic
from providers.base import BaseProvider, ProviderResponse, TextBlock, ToolUseBlock, TokenUsage
from providers.capabilities import ProviderCapabilities
from runtime.token_tracker import get_tracker
from app_config import config

_RETRY_DELAYS = (1, 2, 4)


class AnthropicProvider(BaseProvider):
    capabilities = ProviderCapabilities(
        tool_use=True,
        structured_json_schema=True,
        parallel_tool_calls=False,
        streaming=False,
    )

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key)

    def _chat_impl(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
        json_schema: dict | None = None,
        label: str = "",
    ) -> ProviderResponse:
        # ── Structured output via single-tool trick ──────────────────────
        if json_schema is not None:
            return self._chat_structured(messages, system, json_schema, label)

        last_exc: Exception | None = None
        for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=config.llm.max_tokens,
                    system=system,
                    tools=tools,
                    messages=messages,
                )
                break
            except anthropic.RateLimitError as exc:
                last_exc = exc
                if delay is None:
                    raise
                time.sleep(delay)
        else:
            raise last_exc  # type: ignore[misc]

        return self._parse_response(response, label)

    def _chat_structured(
        self,
        messages: list[dict],
        system: str,
        json_schema: dict,
        label: str,
    ) -> ProviderResponse:
        """Force structured JSON output via a synthetic 'respond' tool."""
        schema = json_schema.get("schema", json_schema)
        respond_tool = {
            "name": "respond",
            "description": "Respond with the required structured data.",
            "input_schema": schema,
        }
        last_exc: Exception | None = None
        for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=config.llm.max_tokens,
                    system=system,
                    tools=[respond_tool],
                    tool_choice={"type": "tool", "name": "respond"},
                    messages=messages,
                )
                break
            except anthropic.RateLimitError as exc:
                last_exc = exc
                if delay is None:
                    raise
                time.sleep(delay)
        else:
            raise last_exc  # type: ignore[misc]

        # Extract the structured input from the respond tool call as a JSON TextBlock.
        content = []
        for block in response.content:
            if block.type == "tool_use" and block.name == "respond":
                content.append(TextBlock(text=json.dumps(block.input, ensure_ascii=False)))
            elif block.type == "text":
                content.append(TextBlock(text=block.text))

        usage = None
        if response.usage:
            usage = TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cache_input_tokens=getattr(response.usage, "cache_read_input_tokens", None),
                cache_creation_tokens=getattr(response.usage, "cache_creation_input_tokens", None),
            )
            get_tracker().record(self.model, label, usage.input_tokens, usage.output_tokens)

        stop_reason = response.stop_reason
        return ProviderResponse(stop_reason=stop_reason, content=content, usage=usage)

    def _parse_response(self, response, label: str) -> ProviderResponse:
        content = []
        for block in response.content:
            if block.type == "text":
                content.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                content.append(ToolUseBlock(id=block.id, name=block.name, input=block.input))

        usage = None
        if response.usage:
            usage = TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cache_input_tokens=getattr(response.usage, "cache_read_input_tokens", None),
                cache_creation_tokens=getattr(response.usage, "cache_creation_input_tokens", None),
            )
            get_tracker().record(self.model, label, usage.input_tokens, usage.output_tokens)

        return ProviderResponse(stop_reason=response.stop_reason, content=content, usage=usage)
