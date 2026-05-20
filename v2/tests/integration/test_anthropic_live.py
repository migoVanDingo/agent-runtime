"""Integration test for AnthropicProvider — hits the real API.

Skipped if ANTHROPIC_API_KEY is not set. Uses claude-haiku-4-5 by default
(cheap, fast, and what the design doc names as the canonical small model).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)


def test_anthropic_simple_call():
    from arc.config import ProviderConfig, RetryConfig
    from arc.providers.anthropic import AnthropicProvider
    from arc.runtime.hooks import LLMRequest, Message

    cfg = ProviderConfig(
        name="anthropic",
        model="claude-haiku-4-5",
        api_key_env="ANTHROPIC_API_KEY",
        base_url=None,
        timeout_seconds=60.0,
        retry=RetryConfig(max_attempts=2, backoff_base_seconds=1.0, backoff_max_seconds=4.0),
        params={"temperature": 0, "max_tokens": 32},
    )

    provider = AnthropicProvider(cfg)
    req = LLMRequest(
        messages=[Message(role="user", content="Reply with exactly: pong")],
        system="You are concise. Reply with exactly what's asked.",
        tools=[],
        model=cfg.model,
        params=cfg.params,
    )
    resp = provider.chat(req)

    text_blocks = [b for b in resp.content if b.type == "text"]
    assert len(text_blocks) >= 1
    assert "pong" in text_blocks[0].text.lower()

    assert resp.input_tokens > 0
    assert resp.output_tokens > 0
    assert isinstance(resp.raw, dict)
    assert "content" in resp.raw


def test_anthropic_tool_use_round_trip():
    """Anthropic calls a tool, we send the result back, it responds."""
    from arc.config import ProviderConfig, RetryConfig
    from arc.providers.anthropic import AnthropicProvider
    from arc.runtime.hooks import (
        ContentBlock, LLMRequest, Message, ToolSpec,
    )

    cfg = ProviderConfig(
        name="anthropic", model="claude-haiku-4-5",
        api_key_env="ANTHROPIC_API_KEY", base_url=None, timeout_seconds=60.0,
        retry=RetryConfig(max_attempts=2, backoff_base_seconds=1.0, backoff_max_seconds=4.0),
        params={"temperature": 0, "max_tokens": 256},
    )
    provider = AnthropicProvider(cfg)

    tools = [ToolSpec(
        name="ls", description="List files at a path. Returns one per line.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "directory to list"}},
            "required": ["path"],
        },
    )]

    # First call: model should call the tool
    req1 = LLMRequest(
        messages=[Message(role="user", content="Use the ls tool on /tmp")],
        system="You have one tool: ls. Use it.",
        tools=tools, model=cfg.model, params=cfg.params,
    )
    resp1 = provider.chat(req1)
    assert resp1.stop_reason == "tool_use"
    tool_calls = [b for b in resp1.content if b.type == "tool_use"]
    assert len(tool_calls) >= 1
    tool_call = tool_calls[0]
    assert tool_call.tool_name == "ls"
    assert tool_call.tool_use_id  # Anthropic provides this

    # Second call: send back a tool result, verify it accepts
    req2 = LLMRequest(
        messages=[
            Message(role="user", content="Use the ls tool on /tmp"),
            Message(role="assistant", content=list(resp1.content)),
            # The loop normally constructs this — we mimic its shape
            Message(role="tool", content=[
                {"function_response": {
                    "name": "ls",
                    "response": {"result": "alpha.txt\nbeta.txt"},
                }}
            ], name="ls"),
        ],
        system="You have one tool: ls. Use it.",
        tools=tools, model=cfg.model, params=cfg.params,
    )
    resp2 = provider.chat(req2)
    # Should be a text response summarizing the listing
    text = "".join(b.text for b in resp2.content if b.type == "text" and b.text)
    assert "alpha" in text.lower() or "beta" in text.lower() or "2" in text


def test_anthropic_via_factory():
    """Provider factory builds AnthropicProvider from config and it works."""
    from arc.config import ProviderConfig, RetryConfig
    from arc.providers import build
    from arc.runtime.hooks import LLMRequest, Message

    cfg = ProviderConfig(
        name="anthropic", model="claude-haiku-4-5",
        api_key_env="ANTHROPIC_API_KEY", base_url=None, timeout_seconds=60.0,
        retry=RetryConfig(max_attempts=2, backoff_base_seconds=1.0, backoff_max_seconds=4.0),
        params={"temperature": 0, "max_tokens": 32},
    )
    provider = build(cfg)
    req = LLMRequest(
        messages=[Message(role="user", content="Reply with exactly: ack")],
        system="Be concise.", tools=[], model=cfg.model, params=cfg.params,
    )
    resp = provider.chat(req)
    text = "".join(b.text for b in resp.content if b.type == "text")
    assert "ack" in text.lower()
