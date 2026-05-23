"""Unit tests for the shared OpenAI-compat translation shim.

Stubbed openai SDK — no network.  The Ollama-specific tests live in
test_ollama.py; everything in here exercises the translation layer that
all OpenAI-compatible providers share.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from arc.config import RetryConfig
from arc.providers.openai_compat import (
    CompatCapabilities,
    OpenAICompatProvider,
)
from arc.runtime.hooks import ContentBlock, LLMRequest, Message, ToolSpec


def _retry() -> RetryConfig:
    return RetryConfig(max_attempts=3, backoff_base_seconds=0.0, backoff_max_seconds=0.0)


def _make_provider(*, capabilities: CompatCapabilities | None = None):
    caps = capabilities or CompatCapabilities()
    with patch("openai.OpenAI") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client
        provider = OpenAICompatProvider(
            base_url="http://test/v1",
            api_key="k",
            model="m",
            retry=_retry(),
            params={},
            capabilities=caps,
        )
    return provider, client


def _mock_completion(
    *,
    text: str = "",
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 7,
    completion_tokens: int = 11,
):
    """Build a mock openai.types.chat.ChatCompletion."""
    msg = MagicMock()
    msg.content = text or None
    if tool_calls:
        oai_calls = []
        for tc in tool_calls:
            ti = MagicMock()
            ti.id = tc["id"]
            fn = MagicMock()
            fn.name = tc["name"]
            fn.arguments = (
                tc["arguments"]
                if isinstance(tc["arguments"], str)
                else json.dumps(tc["arguments"])
            )
            ti.function = fn
            oai_calls.append(ti)
        msg.tool_calls = oai_calls
    else:
        msg.tool_calls = None

    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish_reason

    resp = MagicMock()
    resp.choices = [choice]
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    resp.usage = usage
    resp.model_dump = MagicMock(return_value={
        "choices": [{
            "message": {"content": text, "tool_calls": tool_calls or None},
            "finish_reason": finish_reason,
        }],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    })
    return resp


# ── Request translation ────────────────────────────────────────────────────


def test_system_prompt_is_first_message():
    provider, _ = _make_provider()
    msgs = provider._translate_messages("you are concise", [
        Message(role="user", content="hi"),
    ])
    assert msgs[0] == {"role": "system", "content": "you are concise"}
    assert msgs[1] == {"role": "user", "content": "hi"}


def test_empty_system_prompt_is_omitted():
    provider, _ = _make_provider()
    msgs = provider._translate_messages("", [Message(role="user", content="hi")])
    assert msgs == [{"role": "user", "content": "hi"}]


def test_assistant_text_message_round_trip():
    provider, _ = _make_provider()
    msgs = provider._translate_messages("", [
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
    ])
    assert msgs[1] == {"role": "assistant", "content": "hello"}


def test_assistant_tool_use_becomes_tool_calls():
    provider, _ = _make_provider()
    out = provider._translate_messages("", [
        Message(role="assistant", content=[
            ContentBlock(type="text", text="checking"),
            ContentBlock(
                type="tool_use",
                tool_use_id="call_1",
                tool_name="ls",
                tool_input={"path": "/tmp"},
            ),
        ]),
    ])
    entry = out[0]
    assert entry["role"] == "assistant"
    assert entry["content"] == "checking"
    assert entry["tool_calls"] == [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "ls", "arguments": json.dumps({"path": "/tmp"})},
    }]


def test_tool_result_uses_previous_assistant_id_by_position():
    provider, _ = _make_provider()
    out = provider._translate_messages("", [
        Message(role="assistant", content=[
            ContentBlock(type="tool_use", tool_use_id="abc",
                         tool_name="ls", tool_input={}),
        ]),
        Message(role="tool", content=[
            {"function_response": {"name": "ls", "response": {"result": "a\nb"}}}
        ], name="ls"),
    ])
    assert out[1] == {
        "role": "tool",
        "tool_call_id": "abc",
        "content": "a\nb",
    }


def test_tools_are_translated_to_openai_shape():
    schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}
    out = OpenAICompatProvider._translate_tools([
        ToolSpec(name="ls", description="list", input_schema=schema),
    ])
    assert out == [{
        "type": "function",
        "function": {"name": "ls", "description": "list", "parameters": schema},
    }]


# ── Response translation ───────────────────────────────────────────────────


def test_chat_translates_text_response_and_usage():
    provider, client = _make_provider()
    client.chat.completions.create.return_value = _mock_completion(
        text="hello", finish_reason="stop", prompt_tokens=3, completion_tokens=4,
    )

    resp = provider.chat(LLMRequest(
        messages=[Message(role="user", content="hi")],
        system="", tools=[], model="m", params={},
    ))

    assert len(resp.content) == 1
    assert resp.content[0].type == "text"
    assert resp.content[0].text == "hello"
    assert resp.stop_reason == "end_turn"
    assert resp.input_tokens == 3
    assert resp.output_tokens == 4
    assert resp.raw["usage"]["prompt_tokens"] == 3


def test_chat_translates_tool_call_response():
    provider, client = _make_provider()
    client.chat.completions.create.return_value = _mock_completion(
        text="",
        tool_calls=[{"id": "call_x", "name": "ls", "arguments": '{"path": "."}'}],
        finish_reason="tool_calls",
    )

    resp = provider.chat(LLMRequest(
        messages=[Message(role="user", content="list")],
        system="", tools=[ToolSpec(name="ls", description="d",
                                   input_schema={"type": "object", "properties": {}})],
        model="m", params={},
    ))
    assert resp.stop_reason == "tool_use"
    tool_blocks = [b for b in resp.content if b.type == "tool_use"]
    assert len(tool_blocks) == 1
    assert tool_blocks[0].tool_use_id == "call_x"
    assert tool_blocks[0].tool_name == "ls"
    assert tool_blocks[0].tool_input == {"path": "."}


def test_empty_tool_arguments_string_parses_to_empty_dict():
    provider, client = _make_provider()
    client.chat.completions.create.return_value = _mock_completion(
        tool_calls=[{"id": "c1", "name": "now", "arguments": ""}],
        finish_reason="tool_calls",
    )
    resp = provider.chat(LLMRequest(
        messages=[Message(role="user", content="?")],
        system="", tools=[ToolSpec(name="now", description="d",
                                   input_schema={"type": "object", "properties": {}})],
        model="m", params={},
    ))
    tool_blocks = [b for b in resp.content if b.type == "tool_use"]
    assert tool_blocks[0].tool_input == {}


def test_invalid_tool_arguments_json_raises():
    provider, client = _make_provider()
    client.chat.completions.create.return_value = _mock_completion(
        tool_calls=[{"id": "c1", "name": "ls", "arguments": "not-json"}],
        finish_reason="tool_calls",
    )
    with pytest.raises(RuntimeError, match="invalid JSON tool arguments"):
        provider.chat(LLMRequest(
            messages=[Message(role="user", content="?")],
            system="", tools=[ToolSpec(name="ls", description="d",
                                       input_schema={"type": "object", "properties": {}})],
            model="m", params={},
        ))


@pytest.mark.parametrize("finish,expected", [
    ("stop", "end_turn"),
    ("length", "max_tokens"),
    ("tool_calls", "tool_use"),
    ("content_filter", "other"),
    (None, "other"),
])
def test_stop_reason_mapping(finish, expected):
    assert OpenAICompatProvider._translate_stop_reason(finish) == expected


def test_raw_uses_model_dump():
    provider, client = _make_provider()
    completion = _mock_completion(text="ok")
    client.chat.completions.create.return_value = completion

    resp = provider.chat(LLMRequest(
        messages=[Message(role="user", content="hi")],
        system="", tools=[], model="m", params={},
    ))
    completion.model_dump.assert_called_with(mode="json")
    assert "usage" in resp.raw


# ── Params + retry ─────────────────────────────────────────────────────────


def test_max_tokens_param_routes_through_capability():
    provider, client = _make_provider(
        capabilities=CompatCapabilities(max_tokens_param="max_completion_tokens"),
    )
    client.chat.completions.create.return_value = _mock_completion(text="x")

    provider.chat(LLMRequest(
        messages=[Message(role="user", content="hi")],
        system="", tools=[], model="m", params={"max_tokens": 123},
    ))
    sent = client.chat.completions.create.call_args.kwargs
    assert "max_tokens" not in sent
    assert sent["max_completion_tokens"] == 123


def test_unknown_params_go_to_extra_body():
    provider, client = _make_provider()
    client.chat.completions.create.return_value = _mock_completion(text="x")

    provider.chat(LLMRequest(
        messages=[Message(role="user", content="hi")],
        system="", tools=[], model="m",
        params={"temperature": 0.2, "top_k": 40, "num_ctx": 8192},
    ))
    sent = client.chat.completions.create.call_args.kwargs
    assert sent["temperature"] == 0.2
    assert sent["extra_body"] == {"top_k": 40, "num_ctx": 8192}


def test_tools_disabled_raises_clear_error_when_tools_present():
    provider, _ = _make_provider(
        capabilities=CompatCapabilities(tool_use=False),
    )
    with pytest.raises(RuntimeError, match="doesn't support tool calling"):
        provider.chat(LLMRequest(
            messages=[Message(role="user", content="hi")],
            system="", tools=[ToolSpec(name="ls", description="d",
                                       input_schema={"type": "object", "properties": {}})],
            model="m", params={},
        ))


def test_no_parallel_tool_calls_sets_flag():
    provider, client = _make_provider(
        capabilities=CompatCapabilities(parallel_tool_calls=False),
    )
    client.chat.completions.create.return_value = _mock_completion(text="x")

    provider.chat(LLMRequest(
        messages=[Message(role="user", content="hi")],
        system="", tools=[ToolSpec(name="ls", description="d",
                                   input_schema={"type": "object", "properties": {}})],
        model="m", params={},
    ))
    sent = client.chat.completions.create.call_args.kwargs
    assert sent["parallel_tool_calls"] is False


def test_retry_exhaustion_raises():
    provider, client = _make_provider()
    client.chat.completions.create.side_effect = ConnectionError("boom")

    with pytest.raises(RuntimeError, match="call failed after 3 attempts"):
        provider.chat(LLMRequest(
            messages=[Message(role="user", content="hi")],
            system="", tools=[], model="m", params={},
        ))
    assert client.chat.completions.create.call_count == 3


def test_retry_succeeds_after_transient_failure():
    provider, client = _make_provider()
    client.chat.completions.create.side_effect = [
        ConnectionError("once"),
        _mock_completion(text="ok"),
    ]
    resp = provider.chat(LLMRequest(
        messages=[Message(role="user", content="hi")],
        system="", tools=[], model="m", params={},
    ))
    assert resp.content[0].text == "ok"
    assert client.chat.completions.create.call_count == 2
