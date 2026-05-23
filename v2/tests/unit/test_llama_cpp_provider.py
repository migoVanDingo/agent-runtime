"""Unit tests for LlamaCppProvider (compat + grammar dispatch)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from arc.config import ProviderConfig, RetryConfig
from arc.providers import build
from arc.providers.llama_cpp import LlamaCppProvider
from arc.providers.llama_cpp.provider import (
    _build_grammar_prompt,
    _grammar_response_to_llm_response,
)
from arc.runtime.hooks import ContentBlock, LLMRequest, Message, ToolSpec


def _cfg(**overrides) -> ProviderConfig:
    base = dict(
        name="llama_cpp",
        model="",
        api_key_env="LLAMA_CPP_API_KEY",
        base_url=None,
        timeout_seconds=120.0,
        retry=RetryConfig(max_attempts=2, backoff_base_seconds=0.0, backoff_max_seconds=0.0),
        params={"mode": "compat", "temperature": 0, "max_tokens": 128},
    )
    base.update(overrides)
    return ProviderConfig(**base)


# ── Dispatcher ─────────────────────────────────────────────────────────────


def test_build_returns_llama_cpp_for_known_name():
    with patch("openai.OpenAI"), \
         patch("arc.providers.llama_cpp.provider._preflight"):
        provider = build(_cfg())
    assert isinstance(provider, LlamaCppProvider)
    assert provider.name == "llama_cpp"


def test_unknown_mode_raises():
    with patch("openai.OpenAI"), \
         patch("arc.providers.llama_cpp.provider._preflight"):
        with pytest.raises(ValueError, match="must be 'compat' or 'grammar'"):
            LlamaCppProvider(_cfg(params={"mode": "speculative"}))


def test_compat_mode_uses_openai_compat_provider():
    with patch("openai.OpenAI") as mock_oa, \
         patch("arc.providers.llama_cpp.provider._preflight"):
        provider = LlamaCppProvider(_cfg(params={"mode": "compat", "max_tokens": 64}))
    # Backend was constructed with the OpenAI SDK
    assert mock_oa.called
    base_url = mock_oa.call_args.kwargs["base_url"]
    assert base_url == "http://localhost:8080/v1"
    # The dispatcher exposes the inner backend with the right `name`
    assert provider._impl._backend.name == "llama_cpp"


def test_compat_mode_disables_parallel_tool_calls_by_default():
    """llama-server's compat parallel handling is template-dependent;
    the provider disables it unless the user opts back in."""
    with patch("openai.OpenAI") as mock_oa, \
         patch("arc.providers.llama_cpp.provider._preflight"):
        provider = LlamaCppProvider(_cfg())
    # We can't observe the capabilities through public API, but a chat
    # with tools should set parallel_tool_calls=False on the create call.
    mock_oa.return_value.chat.completions.create.return_value = _stub_oa_completion(text="ok")
    provider.chat(LLMRequest(
        messages=[Message(role="user", content="hi")],
        system="", tools=[ToolSpec(name="ls", description="d",
                                   input_schema={"type": "object", "properties": {}})],
        model="m", params={},
    ))
    sent = mock_oa.return_value.chat.completions.create.call_args.kwargs
    assert sent.get("parallel_tool_calls") is False


# ── Grammar mode ───────────────────────────────────────────────────────────


def test_grammar_mode_calls_native_completion():
    with patch("arc.providers.llama_cpp.provider.post_completion") as mock_post, \
         patch("arc.providers.llama_cpp.provider._preflight"):
        mock_post.return_value = _stub_completion_body(content="ANSWER:\nhello")
        provider = LlamaCppProvider(_cfg(params={"mode": "grammar", "max_tokens": 64}))
        resp = provider.chat(LLMRequest(
            messages=[Message(role="user", content="hi")],
            system="you are concise",
            tools=[],
            model="",
            params={"temperature": 0, "max_tokens": 64},
        ))

    assert mock_post.called
    payload = mock_post.call_args.kwargs["payload"]
    assert "grammar" in payload
    assert payload["n_predict"] == 64
    assert payload["temperature"] == 0.0

    assert resp.stop_reason == "end_turn"
    assert resp.content[0].type == "text"
    assert resp.content[0].text == "hello"


def test_grammar_mode_tool_call_response_parsed():
    body = _stub_completion_body(
        content='TOOL:\n{"name": "ls", "input": {"path": "/tmp"}}',
        tokens_evaluated=42,
        tokens_predicted=12,
    )
    resp = _grammar_response_to_llm_response(body, "grammar-text")
    assert resp.stop_reason == "tool_use"
    tool_blocks = [b for b in resp.content if b.type == "tool_use"]
    assert len(tool_blocks) == 1
    assert tool_blocks[0].tool_name == "ls"
    assert tool_blocks[0].tool_input == {"path": "/tmp"}
    # tool_use_id is synthesized — non-empty string
    assert tool_blocks[0].tool_use_id
    assert resp.input_tokens == 42
    assert resp.output_tokens == 12


def test_grammar_mode_raw_carries_mode_metadata():
    body = _stub_completion_body(
        content="ANSWER:\nhi",
        timings={"predicted_per_token_ms": 18.4},
    )
    resp = _grammar_response_to_llm_response(body, "G" * 50)
    assert resp.raw["_arc_llama_cpp"]["mode"] == "grammar"
    assert resp.raw["_arc_llama_cpp"]["grammar_size_bytes"] == 50
    assert resp.raw["_arc_llama_cpp"]["predicted_per_token_ms"] == 18.4


def test_grammar_mode_malformed_tool_payload_raises():
    body = _stub_completion_body(content="TOOL:\nnot-json")
    with pytest.raises(RuntimeError, match="didn't parse as JSON"):
        _grammar_response_to_llm_response(body, "G")


def test_grammar_mode_no_known_prefix_falls_back_to_text():
    body = _stub_completion_body(content="just some text without prefix")
    resp = _grammar_response_to_llm_response(body, "G")
    assert resp.content[0].type == "text"
    assert resp.content[0].text == "just some text without prefix"


# ── Prompt construction ───────────────────────────────────────────────────


def test_postamble_injects_tool_list():
    prompt = _build_grammar_prompt(
        system="you are concise",
        messages=[Message(role="user", content="hi")],
        tools=[
            ToolSpec(name="ls", description="List files",
                     input_schema={"type": "object", "properties": {}}),
            ToolSpec(name="bash_exec", description="Run bash",
                     input_schema={"type": "object", "properties": {}}),
        ],
    )
    assert "you are concise" in prompt
    assert "Available tools:" in prompt
    assert "- ls: List files" in prompt
    assert "- bash_exec: Run bash" in prompt
    # Ends with the Assistant cue
    assert prompt.rstrip().endswith("Assistant:")


def test_postamble_handles_no_tools():
    prompt = _build_grammar_prompt(system="", messages=[
        Message(role="user", content="hi")
    ], tools=[])
    assert "no tools available" in prompt


def test_assistant_history_serialized_as_grammar_format():
    prompt = _build_grammar_prompt(
        system="",
        messages=[
            Message(role="user", content="run ls"),
            Message(role="assistant", content=[
                ContentBlock(type="tool_use", tool_use_id="x", tool_name="ls",
                             tool_input={"path": "/"}),
            ]),
            Message(role="tool", content=[
                {"function_response": {"name": "ls", "response": {"result": "a\nb"}}}
            ], name="ls"),
            Message(role="assistant", content="done"),
        ],
        tools=[ToolSpec(name="ls", description="list",
                        input_schema={"type": "object", "properties": {}})],
    )
    assert 'TOOL:\n{"name": "ls", "input": {"path": "/"}}' in prompt
    assert "Tool result: a\nb" in prompt
    assert "ANSWER:\ndone" in prompt


# ── Helpers ────────────────────────────────────────────────────────────────


def _stub_oa_completion(*, text: str):
    """OpenAI-SDK-shaped completion stub for compat path."""
    msg = MagicMock()
    msg.content = text or None
    msg.tool_calls = None
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    usage = MagicMock()
    usage.prompt_tokens = 1
    usage.completion_tokens = 1
    resp.usage = usage
    resp.model_dump = MagicMock(return_value={"choices": [{"message": {"content": text}}]})
    return resp


def _stub_completion_body(
    *,
    content: str = "",
    tokens_evaluated: int = 0,
    tokens_predicted: int = 0,
    timings: dict | None = None,
):
    body = {
        "content": content,
        "tokens_evaluated": tokens_evaluated,
        "tokens_predicted": tokens_predicted,
    }
    if timings is not None:
        body["timings"] = timings
    return body
