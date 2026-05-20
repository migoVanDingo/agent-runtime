"""Unit tests for AnthropicProvider.

Mock the anthropic SDK so these run without network or API key. The
integration test in tests/integration/ hits the real API.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from arc.config import ProviderConfig, RetryConfig
from arc.providers import build
from arc.providers.anthropic import AnthropicProvider
from arc.runtime.hooks import ContentBlock, LLMRequest, Message, ToolSpec


def _cfg(**overrides) -> ProviderConfig:
    base = dict(
        name="anthropic",
        model="claude-haiku-4-5",
        api_key_env="ANTHROPIC_API_KEY",
        base_url=None,
        timeout_seconds=60.0,
        retry=RetryConfig(max_attempts=3, backoff_base_seconds=0.01, backoff_max_seconds=0.05),
        params={"temperature": 0, "max_tokens": 100},
    )
    base.update(overrides)
    return ProviderConfig(**base)


def _mock_response(
    *,
    text: str = "ok",
    tool_use: dict | None = None,
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 5,
):
    """Build a mock anthropic.types.Message with the fields we care about."""
    blocks = []
    if text:
        b = MagicMock()
        b.type = "text"
        b.text = text
        blocks.append(b)
    if tool_use:
        b = MagicMock()
        b.type = "tool_use"
        b.id = tool_use["id"]
        b.name = tool_use["name"]
        b.input = tool_use.get("input", {})
        blocks.append(b)

    resp = MagicMock()
    resp.content = blocks
    resp.stop_reason = stop_reason
    resp.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    resp.model_dump = MagicMock(return_value={
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    })
    return resp


# ── Factory ─────────────────────────────────────────────────────────────────


def test_build_returns_anthropic_for_known_name(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with patch("anthropic.Anthropic"):
        p = build(_cfg())
    assert isinstance(p, AnthropicProvider)
    assert p.name == "anthropic"


def test_build_unknown_provider_raises():
    with pytest.raises(ValueError, match="unknown provider"):
        build(_cfg(name="claude"))


# ── Initialization ─────────────────────────────────────────────────────────


def test_missing_api_key_env_raises_clearly(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider(_cfg())


# ── chat() — basic ─────────────────────────────────────────────────────────


@patch("anthropic.Anthropic")
def test_chat_simple_text(mock_client_cls, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_response(text="hello back")

    provider = AnthropicProvider(_cfg())
    req = LLMRequest(
        messages=[Message(role="user", content="hello")],
        system="you are concise",
        tools=[],
        model="claude-haiku-4-5",
        params={"temperature": 0, "max_tokens": 50},
    )
    resp = provider.chat(req)

    assert len(resp.content) == 1
    assert resp.content[0].type == "text"
    assert resp.content[0].text == "hello back"
    assert resp.stop_reason == "end_turn"
    assert resp.input_tokens == 10
    assert resp.output_tokens == 5
    assert "content" in resp.raw

    # Verify system was passed
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["system"] == "you are concise"
    assert call_kwargs["max_tokens"] == 50


@patch("anthropic.Anthropic")
def test_chat_max_tokens_falls_back_to_default_when_missing(mock_client_cls, monkeypatch):
    """Anthropic SDK requires max_tokens — we must always send one."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_response()

    provider = AnthropicProvider(_cfg(params={"temperature": 0}))  # no max_tokens
    req = LLMRequest(messages=[Message(role="user", content="x")],
                     system="", tools=[], model="x", params={"temperature": 0})
    provider.chat(req)
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["max_tokens"] == 4096  # fallback


@patch("anthropic.Anthropic")
def test_chat_with_tool_use(mock_client_cls, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_response(
        text="", tool_use={"id": "call_1", "name": "ls", "input": {"path": "."}},
        stop_reason="tool_use",
    )

    provider = AnthropicProvider(_cfg())
    req = LLMRequest(
        messages=[Message(role="user", content="what files?")],
        system="use ls",
        tools=[ToolSpec(name="ls", description="list",
                        input_schema={"type": "object",
                                      "properties": {"path": {"type": "string"}},
                                      "required": []})],
        model="claude-haiku-4-5",
        params={},
    )
    resp = provider.chat(req)
    assert resp.stop_reason == "tool_use"
    tool_blocks = [b for b in resp.content if b.type == "tool_use"]
    assert len(tool_blocks) == 1
    assert tool_blocks[0].tool_name == "ls"
    assert tool_blocks[0].tool_use_id == "call_1"
    assert tool_blocks[0].tool_input == {"path": "."}


# ── Translation: messages → Anthropic format ──────────────────────────────


@patch("anthropic.Anthropic")
def test_translate_simple_user_message(mock_client_cls, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    p = AnthropicProvider(_cfg())
    out = p._translate_messages([Message(role="user", content="hi")])
    assert out == [{"role": "user", "content": "hi"}]


@patch("anthropic.Anthropic")
def test_translate_assistant_text_message(mock_client_cls, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    p = AnthropicProvider(_cfg())
    out = p._translate_messages([
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
    ])
    assert out == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
    ]


@patch("anthropic.Anthropic")
def test_translate_assistant_with_tool_use_emits_id(mock_client_cls, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    p = AnthropicProvider(_cfg())
    msgs = [
        Message(role="user", content="list"),
        Message(role="assistant", content=[
            ContentBlock(type="text", text="checking"),
            ContentBlock(type="tool_use", tool_use_id="abc", tool_name="ls",
                         tool_input={"path": "/tmp"}),
        ]),
    ]
    out = p._translate_messages(msgs)
    assistant = out[1]
    assert assistant["role"] == "assistant"
    blocks = assistant["content"]
    assert blocks[0] == {"type": "text", "text": "checking"}
    assert blocks[1] == {
        "type": "tool_use", "id": "abc", "name": "ls", "input": {"path": "/tmp"},
    }


@patch("anthropic.Anthropic")
def test_translate_tool_result_matches_position(mock_client_cls, monkeypatch):
    """Tool message → user message with tool_result, ID matched from prev assistant."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    p = AnthropicProvider(_cfg())
    msgs = [
        Message(role="user", content="list"),
        Message(role="assistant", content=[
            ContentBlock(type="tool_use", tool_use_id="tcl_1", tool_name="ls",
                         tool_input={"path": "/tmp"}),
        ]),
        Message(role="tool", content=[
            {"function_response": {"name": "ls", "response": {"result": "a\nb"}}}
        ], name="ls"),
    ]
    out = p._translate_messages(msgs)
    assert len(out) == 3
    tool_result_msg = out[2]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"] == [{
        "type": "tool_result", "tool_use_id": "tcl_1", "content": "a\nb",
    }]


@patch("anthropic.Anthropic")
def test_translate_parallel_tool_results_match_positions(mock_client_cls, monkeypatch):
    """Two tool_use blocks followed by two tool results → matched by order."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    p = AnthropicProvider(_cfg())
    msgs = [
        Message(role="user", content="do stuff"),
        Message(role="assistant", content=[
            ContentBlock(type="tool_use", tool_use_id="a", tool_name="ls", tool_input={"path": "/"}),
            ContentBlock(type="tool_use", tool_use_id="b", tool_name="ls", tool_input={"path": "/tmp"}),
        ]),
        Message(role="tool", content=[
            {"function_response": {"name": "ls", "response": {"result": "root output"}}}
        ], name="ls"),
        Message(role="tool", content=[
            {"function_response": {"name": "ls", "response": {"result": "tmp output"}}}
        ], name="ls"),
    ]
    out = p._translate_messages(msgs)
    assert out[2]["content"][0]["tool_use_id"] == "a"
    assert out[2]["content"][0]["content"] == "root output"
    assert out[3]["content"][0]["tool_use_id"] == "b"
    assert out[3]["content"][0]["content"] == "tmp output"


@patch("anthropic.Anthropic")
def test_translate_tool_result_with_no_pending_id_falls_back(mock_client_cls, monkeypatch):
    """If the loop somehow appends a tool message with no preceding tool_use,
    the provider emits 'unknown' rather than crashing. Anthropic will return
    400 — that's better than silently sending wrong data."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    p = AnthropicProvider(_cfg())
    msgs = [
        Message(role="user", content="x"),
        Message(role="tool", content=[
            {"function_response": {"name": "x", "response": {"result": "y"}}}
        ], name="x"),
    ]
    out = p._translate_messages(msgs)
    assert out[1]["content"][0]["tool_use_id"] == "unknown"


# ── Tool translation ───────────────────────────────────────────────────────


@patch("anthropic.Anthropic")
def test_translate_tools(mock_client_cls, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    p = AnthropicProvider(_cfg())
    tools = [
        ToolSpec(name="ls", description="list",
                 input_schema={"type": "object", "properties": {"path": {"type": "string"}},
                               "required": []}),
    ]
    out = p._translate_tools(tools)
    assert out == [{
        "name": "ls", "description": "list",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                         "required": []},
    }]


# ── Stop reason translation ────────────────────────────────────────────────


@patch("anthropic.Anthropic")
def test_stop_reasons_pass_through(mock_client_cls, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    p = AnthropicProvider(_cfg())
    assert p._translate_stop_reason("end_turn") == "end_turn"
    assert p._translate_stop_reason("tool_use") == "tool_use"
    assert p._translate_stop_reason("max_tokens") == "max_tokens"


@patch("anthropic.Anthropic")
def test_unknown_stop_reason_maps_to_other(mock_client_cls, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    p = AnthropicProvider(_cfg())
    assert p._translate_stop_reason("stop_sequence") == "other"
    assert p._translate_stop_reason("pause_turn") == "other"
    assert p._translate_stop_reason(None) == "other"


# ── Retry ─────────────────────────────────────────────────────────────────


@patch("anthropic.Anthropic")
def test_chat_retries_then_succeeds(mock_client_cls, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.messages.create.side_effect = [
        RuntimeError("transient 1"),
        RuntimeError("transient 2"),
        _mock_response(text="success"),
    ]
    provider = AnthropicProvider(_cfg())
    req = LLMRequest(messages=[Message(role="user", content="x")],
                     system="", tools=[], model="x", params={"max_tokens": 50})
    resp = provider.chat(req)
    assert resp.content[0].text == "success"
    assert mock_client.messages.create.call_count == 3


@patch("anthropic.Anthropic")
def test_chat_exhausts_retries_raises(mock_client_cls, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.messages.create.side_effect = RuntimeError("always")
    provider = AnthropicProvider(_cfg())
    req = LLMRequest(messages=[Message(role="user", content="x")],
                     system="", tools=[], model="x", params={"max_tokens": 50})
    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        provider.chat(req)
    assert mock_client.messages.create.call_count == 3
