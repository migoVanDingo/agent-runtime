import anthropic
from providers.base import BaseProvider, ProviderResponse, TextBlock, ToolUseBlock
from app_config import config


class AnthropicProvider(BaseProvider):

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key)

    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
    ) -> ProviderResponse:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=config.llm.max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )

        content = []
        for block in response.content:
            if block.type == "text":
                content.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                content.append(ToolUseBlock(id=block.id, name=block.name, input=block.input))

        return ProviderResponse(stop_reason=response.stop_reason, content=content)
