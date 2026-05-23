"""Integration test for OllamaProvider — hits a real Ollama server.

Skipped unless the `OLLAMA_HOST` env var points at a running server.
The runtime usually runs co-located with Ollama, so the canonical setup is:

    OLLAMA_HOST=http://localhost:11434 python3 -m pytest tests/integration/test_ollama_live.py

Set `OLLAMA_MODEL` (default: llama3.1:8b) to pick the tag to exercise; the
test will skip with a hint if that tag isn't pulled.
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
    not os.environ.get("OLLAMA_HOST"),
    reason="OLLAMA_HOST not set",
)


def _base_url() -> str:
    host = os.environ["OLLAMA_HOST"].rstrip("/")
    if host.endswith("/v1"):
        return host
    return f"{host}/v1"


def _cfg(model: str):
    from arc.config import ProviderConfig, RetryConfig
    return ProviderConfig(
        name="ollama",
        model=model,
        api_key_env="OLLAMA_API_KEY",
        base_url=_base_url(),
        timeout_seconds=300.0,  # local inference can be slow on first call
        retry=RetryConfig(max_attempts=2, backoff_base_seconds=1, backoff_max_seconds=4),
        params={"temperature": 0, "max_tokens": 128},
    )


def test_ollama_simple_chat():
    from arc.providers.ollama import OllamaProvider
    from arc.runtime.hooks import LLMRequest, Message

    model = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
    provider = OllamaProvider(_cfg(model))
    resp = provider.chat(LLMRequest(
        messages=[Message(role="user", content="say the single word 'pong'")],
        system="reply with one word.",
        tools=[],
        model=model,
        params={"temperature": 0, "max_tokens": 8},
    ))

    text_blocks = [b for b in resp.content if b.type == "text"]
    assert text_blocks, "expected at least one text block"
    assert resp.stop_reason in ("end_turn", "max_tokens", "other")
    assert resp.input_tokens >= 0
    assert resp.raw  # byte-fidelity carrier present


def test_ollama_tool_use_round_trip():
    from arc.providers.ollama import OllamaProvider
    from arc.runtime.hooks import LLMRequest, Message, ToolSpec

    model = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
    provider = OllamaProvider(_cfg(model))
    resp = provider.chat(LLMRequest(
        messages=[Message(role="user", content="list the files in /tmp using the ls tool")],
        system="you have one tool, ls. Use it.",
        tools=[ToolSpec(
            name="ls",
            description="List files in a directory.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )],
        model=model,
        params={"temperature": 0, "max_tokens": 64},
    ))
    tool_calls = [b for b in resp.content if b.type == "tool_use"]
    assert tool_calls, f"expected a tool call, got: {resp.content!r}"
    assert tool_calls[0].tool_name == "ls"
    assert isinstance(tool_calls[0].tool_input, dict)
