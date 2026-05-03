import time
import anthropic
from providers.base import BaseProvider, ProviderResponse, TextBlock, ToolUseBlock, TokenUsage
from runtime.token_tracker import get_tracker
from app_config import config

_RETRY_DELAYS = (1, 2, 4)


class AnthropicProvider(BaseProvider):

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key)

    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
        json_schema: dict | None = None,
        label: str = "",
    ) -> ProviderResponse:
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
            )
            get_tracker().record(self.model, label, usage.input_tokens, usage.output_tokens)

        return ProviderResponse(stop_reason=response.stop_reason, content=content, usage=usage)
