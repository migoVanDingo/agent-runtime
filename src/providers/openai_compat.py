import json
import time
import openai
from providers.base import BaseProvider, ProviderResponse, TextBlock, ToolUseBlock, TokenUsage
from providers.capabilities import ProviderCapabilities
from runtime.token_tracker import get_tracker
from app_config import config
from logger import get_logger

_RETRY_DELAYS = (1, 2, 4)
_MAX_TOOL_CALLS = 128

logger = get_logger(__name__)


class OpenAICompatibleProvider(BaseProvider):
    """Shared translation layer for providers that speak the OpenAI SDK format.

    Subclasses set self.client and self.model in their __init__.
    """

    client: openai.OpenAI
    model: str
    capabilities = ProviderCapabilities(
        tool_use=True,
        structured_json_schema=True,
        parallel_tool_calls=True,
        streaming=False,
    )

    def _chat_impl(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
        json_schema: dict | None = None,
        label: str = "",
    ) -> ProviderResponse:
        openai_messages = self._translate_messages(messages, system)
        openai_tools = self._translate_tools(tools)

        # gpt-5 and newer o-series models use max_completion_tokens;
        # older models use max_tokens. Detect by prefix.
        _new_token_param = (
            self.model.startswith(("gpt-5", "gpt-4o", "o1", "o3", "o4"))
        )
        _token_key = "max_completion_tokens" if _new_token_param else "max_tokens"
        kwargs: dict = {
            "model": self.model,
            "messages": openai_messages,
            _token_key: config.llm.max_tokens,
        }
        if openai_tools:
            kwargs["tools"] = openai_tools
        if json_schema is not None:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": json_schema}

        last_exc: Exception | None = None
        for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
            try:
                response = self.client.chat.completions.create(**kwargs)
                break
            except openai.RateLimitError as exc:
                last_exc = exc
                if delay is None:
                    raise
                time.sleep(delay)
        else:
            raise last_exc  # type: ignore[misc]
        result = self._translate_response(response)

        if response.usage:
            result.usage = TokenUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )
            get_tracker().record(self.model, label, result.usage.input_tokens, result.usage.output_tokens)

        return result

    def stream_completion(
        self,
        messages: list[dict],
        system: str,
        label: str = "",
    ):
        """Stream a no-tool completion, yielding text chunks as they arrive."""
        openai_messages = self._translate_messages(messages, system)
        _new_token_param = self.model.startswith(("gpt-5", "gpt-4o", "o1", "o3", "o4"))
        _token_key = "max_completion_tokens" if _new_token_param else "max_tokens"
        kwargs: dict = {
            "model": self.model,
            "messages": openai_messages,
            _token_key: config.llm.max_tokens,
        }
        stream = self.client.chat.completions.create(**kwargs, stream=True)
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def _translate_messages(self, messages: list[dict], system: str) -> list[dict]:
        result = [{"role": "system", "content": system}]
        # Track tool_call IDs that were dropped due to the 128-entry limit.
        # Their corresponding tool_result messages must be skipped or the API
        # will reject the request with a "tool_call_id not found" error.
        dropped_tool_call_ids: set[str] = set()

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if role == "user":
                if isinstance(content, str):
                    result.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    for block in content:
                        if block.get("type") == "tool_result":
                            tc_id = block["tool_use_id"]
                            if tc_id in dropped_tool_call_ids:
                                continue  # orphaned — skip to avoid API error
                            result.append({
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "content": block["content"],
                            })

            elif role == "assistant":
                if isinstance(content, str):
                    result.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    text_parts = [b["text"] for b in content if b.get("type") == "text"]
                    tool_uses = [b for b in content if b.get("type") == "tool_use"]

                    if len(tool_uses) > _MAX_TOOL_CALLS:
                        dropped = tool_uses[_MAX_TOOL_CALLS:]
                        tool_uses = tool_uses[:_MAX_TOOL_CALLS]
                        for b in dropped:
                            dropped_tool_call_ids.add(b["id"])
                        logger.warning(
                            f"tool_calls truncated: {len(dropped)} entries dropped "
                            f"(limit={_MAX_TOOL_CALLS})"
                        )

                    tool_calls = [
                        {
                            "id": b["id"],
                            "type": "function",
                            "function": {
                                "name": b["name"],
                                "arguments": json.dumps(b["input"]),
                            },
                        }
                        for b in tool_uses
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
