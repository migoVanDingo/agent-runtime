"""Unit tests for GeminiProvider.

These mock the google.genai SDK so they run without network or API key.
The integration test in tests/integration/ hits the real API.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from arc.config import ProviderConfig, RetryConfig
from arc.providers import build
from arc.providers.gemini import GeminiProvider
from arc.runtime.hooks import ContentBlock, LLMRequest, Message, ToolSpec


# ── Fixtures ───────────────────────────────────────────────────────────────


def _cfg(**overrides) -> ProviderConfig:
    base = dict(
        name="gemini",
        model="gemini-3.1-flash-lite-preview",
        api_key_env="GEMINI_API_KEY",
        base_url=None,
        timeout_seconds=60.0,
        retry=RetryConfig(max_attempts=3, backoff_base_seconds=0.01, backoff_max_seconds=0.05),
        params={"temperature": 0, "max_tokens": 100},
    )
    base.update(overrides)
    return ProviderConfig(**base)


def _mock_gemini_response(text: str = "ok", function_call: dict | None = None,
                          finish_reason: str = "STOP",
                          input_tokens: int = 10, output_tokens: int = 5):
    """Build a mock GenerateContentResponse with the fields we care about."""
    parts = []
    if text:
        part = MagicMock()
        part.text = text
        part.function_call = None
        parts.append(part)
    if function_call:
        part = MagicMock()
        part.text = None
        fc = MagicMock()
        fc.id = function_call.get("id", function_call["name"])
        fc.name = function_call["name"]
        fc.args = function_call.get("args", {})
        part.function_call = fc
        parts.append(part)

    candidate = MagicMock()
    candidate.content.parts = parts
    candidate.finish_reason = MagicMock()
    candidate.finish_reason.value = finish_reason

    resp = MagicMock()
    resp.candidates = [candidate]
    resp.usage_metadata = MagicMock(
        prompt_token_count=input_tokens,
        candidates_token_count=output_tokens,
    )
    resp.model_dump = MagicMock(return_value={
        "candidates": [{"content": {"parts": [{"text": text}] if text else []}}],
        "usage_metadata": {"prompt_token_count": input_tokens,
                           "candidates_token_count": output_tokens},
    })
    return resp


# ── Factory ─────────────────────────────────────────────────────────────────


def test_build_returns_gemini_for_known_name(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    with patch("google.genai.Client"):
        p = build(_cfg())
    assert isinstance(p, GeminiProvider)
    assert p.name == "gemini"


def test_build_unknown_provider_raises(monkeypatch):
    with pytest.raises(ValueError, match="unknown provider"):
        build(_cfg(name="claude-x"))


# ── Initialization ─────────────────────────────────────────────────────────


def test_missing_api_key_env_raises_clearly(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiProvider(_cfg())


# ── chat() — basic ─────────────────────────────────────────────────────────


@patch("google.genai.Client")
def test_chat_simple_text(mock_client_cls, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.return_value = _mock_gemini_response(text="hello back")

    provider = GeminiProvider(_cfg())
    req = LLMRequest(
        messages=[Message(role="user", content="hello")],
        system="you are concise",
        tools=[],
        model="gemini-3.1-flash-lite-preview",
        params={"temperature": 0, "max_tokens": 50},
    )
    resp = provider.chat(req)

    assert len(resp.content) == 1
    assert resp.content[0].type == "text"
    assert resp.content[0].text == "hello back"
    assert resp.stop_reason == "end_turn"
    assert resp.input_tokens == 10
    assert resp.output_tokens == 5
    assert "candidates" in resp.raw  # raw dict captured for replay


@patch("google.genai.Client")
def test_chat_with_function_call(mock_client_cls, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.return_value = _mock_gemini_response(
        text="",
        function_call={"name": "ls", "args": {"path": "."}, "id": "call_1"},
        finish_reason="STOP",
    )

    provider = GeminiProvider(_cfg())
    req = LLMRequest(
        messages=[Message(role="user", content="what files?")],
        system="use the ls tool",
        tools=[ToolSpec(name="ls", description="list files",
                        input_schema={"type": "object",
                                      "properties": {"path": {"type": "string"}},
                                      "required": ["path"]})],
        model="gemini-3.1-flash-lite-preview",
        params={},
    )
    resp = provider.chat(req)

    # Function call presence overrides "STOP" → tool_use
    assert resp.stop_reason == "tool_use"
    tool_blocks = [b for b in resp.content if b.type == "tool_use"]
    assert len(tool_blocks) == 1
    assert tool_blocks[0].tool_name == "ls"
    assert tool_blocks[0].tool_input == {"path": "."}


# ── Translation helpers ───────────────────────────────────────────────────


@patch("google.genai.Client")
def test_messages_to_contents_simple_text(mock_client_cls, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    p = GeminiProvider(_cfg())
    msgs = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
    ]
    from arc.providers._gemini_translation import messages_to_contents
    contents = messages_to_contents(msgs)
    assert contents == [
        {"role": "user", "parts": [{"text": "hi"}]},
        {"role": "model", "parts": [{"text": "hello"}]},
    ]


@patch("google.genai.Client")
def test_messages_to_contents_with_tool_call(mock_client_cls, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    p = GeminiProvider(_cfg())
    msgs = [Message(role="assistant", content=[
        ContentBlock(type="text", text="let me check"),
        ContentBlock(type="tool_use", tool_name="ls", tool_input={"path": "/tmp"}),
    ])]
    from arc.providers._gemini_translation import messages_to_contents
    contents = messages_to_contents(msgs)
    assert contents == [{
        "role": "model",
        "parts": [
            {"text": "let me check"},
            {"function_call": {"name": "ls", "args": {"path": "/tmp"}}},
        ],
    }]


@patch("google.genai.Client")
def test_translate_stop_reason_max_tokens(mock_client_cls, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.return_value = _mock_gemini_response(
        text="truncated...", finish_reason="MAX_TOKENS"
    )
    provider = GeminiProvider(_cfg())
    req = LLMRequest(messages=[Message(role="user", content="x")],
                     system="", tools=[], model="x", params={})
    resp = provider.chat(req)
    assert resp.stop_reason == "max_tokens"


# ── Retry ─────────────────────────────────────────────────────────────────


@patch("google.genai.Client")
def test_chat_retries_then_succeeds(mock_client_cls, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    # Fail twice, then succeed
    mock_client.models.generate_content.side_effect = [
        RuntimeError("transient 1"),
        RuntimeError("transient 2"),
        _mock_gemini_response(text="success"),
    ]
    provider = GeminiProvider(_cfg())
    req = LLMRequest(messages=[Message(role="user", content="x")],
                     system="", tools=[], model="x", params={})
    resp = provider.chat(req)
    assert resp.content[0].text == "success"
    assert mock_client.models.generate_content.call_count == 3


@patch("google.genai.Client")
def test_chat_exhausts_retries_raises(mock_client_cls, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.side_effect = RuntimeError("always")
    provider = GeminiProvider(_cfg())
    req = LLMRequest(messages=[Message(role="user", content="x")],
                     system="", tools=[], model="x", params={})
    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        provider.chat(req)
    assert mock_client.models.generate_content.call_count == 3


# ── Schema sanitization (Gemini rejects anyOf / additionalProperties) ────────


def test_sanitize_flattens_nullable_union():
    from arc.providers._gemini_translation import sanitize_gemini_schema

    out = sanitize_gemini_schema(
        {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None})
    assert out["type"] == "string"
    assert out["nullable"] is True
    assert "anyOf" not in out


def test_sanitize_strips_additional_properties():
    from arc.providers._gemini_translation import sanitize_gemini_schema

    out = sanitize_gemini_schema({"type": "object", "additionalProperties": True})
    assert "additionalProperties" not in out
    assert out["type"] == "object"


def test_sanitize_recurses_into_array_items_and_properties():
    from arc.providers._gemini_translation import sanitize_gemini_schema

    # The exact shape MCP/FastMCP emits for `mounts: list[dict] | None`.
    schema = {
        "type": "object",
        "properties": {
            "mounts": {"anyOf": [
                {"items": {"additionalProperties": True, "type": "object"}, "type": "array"},
                {"type": "null"}]},
        },
    }
    out = sanitize_gemini_schema(schema)
    m = out["properties"]["mounts"]
    assert m["type"] == "array" and m["nullable"] is True
    assert m["items"]["type"] == "object"
    assert "additionalProperties" not in m["items"]


def test_tools_to_gemini_emits_no_rejected_keywords():
    import json

    from arc.providers._gemini_translation import tools_to_gemini

    schema = {
        "type": "object", "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "env": {"anyOf": [{"additionalProperties": True, "type": "object"},
                              {"type": "null"}]},
            "ports": {"anyOf": [{"items": {"type": "string"}, "type": "array"},
                                {"type": "null"}]},
        },
    }
    gt = tools_to_gemini([ToolSpec(name="container_ensure", description="d",
                                   input_schema=schema)])
    params = gt[0].function_declarations[0].parameters
    # exclude_none: a null field on the Schema proto isn't sent to the API; we
    # only care that no union/additionalProperties value is actually POPULATED.
    dumped = params.model_dump(exclude_none=True) if hasattr(params, "model_dump") else params
    blob = json.dumps(dumped, default=str)
    assert "any_of" not in blob and "anyOf" not in blob
    assert "additional_properties" not in blob and "additionalProperties" not in blob
    # optional fields survived as nullable, and array items are intact
    assert dumped["properties"]["env"]["nullable"] is True
    assert dumped["properties"]["ports"]["items"]["type"] in ("STRING", "string")
