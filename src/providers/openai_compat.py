import json
import openai
from providers.base import BaseProvider, ProviderResponse, TextBlock, ToolUseBlock, TokenUsage
from runtime.token_tracker import get_tracker
from app_config import config


class OpenAICompatibleProvider(BaseProvider):
    """Shared translation layer for providers that speak the OpenAI SDK format.

    Subclasses set self.client and self.model in their __init__.
    """

    client: openai.OpenAI
    model: str

    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
        json_schema: dict | None = None,
        label: str = "",
    ) -> ProviderResponse:
        openai_messages = self._translate_messages(messages, system)
        openai_tools = self._translate_tools(tools)

        kwargs: dict = {
            "model": self.model,
            "messages": openai_messages,
            "max_tokens": config.llm.max_tokens,
        }
        if openai_tools:
            kwargs["tools"] = openai_tools
        if json_schema is not None:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": json_schema}

        response = self.client.chat.completions.create(**kwargs)
        result = self._translate_response(response)

        if response.usage:
            result.usage = TokenUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )
            get_tracker().record(self.model, label, result.usage.input_tokens, result.usage.output_tokens)

        return result

    def _translate_messages(self, messages: list[dict], system: str) -> list[dict]:
        result = [{"role": "system", "content": system}]

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if role == "user":
                if isinstance(content, str):
                    result.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    for block in content:
                        if block.get("type") == "tool_result":
                            result.append({
                                "role": "tool",
                                "tool_call_id": block["tool_use_id"],
                                "content": block["content"],
                            })

            elif role == "assistant":
                if isinstance(content, str):
                    result.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    text_parts = [b["text"] for b in content if b.get("type") == "text"]
                    tool_calls = [
                        {
                            "id": b["id"],
                            "type": "function",
                            "function": {
                                "name": b["name"],
                                "arguments": json.dumps(b["input"]),
                            },
                        }
                        for b in content
                        if b.get("type") == "tool_use"
                    ]

                    assistant_msg: dict = {"role": "assistant"}
                    assistant_msg["content"] = " ".join(text_parts) if text_parts else ""
                    if tool_calls:
                        assistant_msg["tool_calls"] = tool_calls
                    result.append(assistant_msg)

        return result

    def _translate_tools(self, tools: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    def _translate_response(self, response) -> ProviderResponse:
        choice = response.choices[0]
        message = choice.message
        finish_reason = choice.finish_reason

        if finish_reason == "tool_calls":
            stop_reason = "tool_use"
        elif finish_reason == "length":
            stop_reason = "max_tokens"
        else:
            stop_reason = "end_turn"

        content = []

        if message.content:
            content.append(TextBlock(text=message.content))

        if message.tool_calls:
            for tc in message.tool_calls:
                content.append(ToolUseBlock(
                    id=tc.id,
                    name=tc.function.name,
                    input=json.loads(tc.function.arguments),
                ))

        return ProviderResponse(stop_reason=stop_reason, content=content)
