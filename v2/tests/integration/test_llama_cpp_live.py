"""Integration test for LlamaCppProvider — hits a real `llama-server`.

Skipped unless `LLAMA_CPP_HOST` is set, e.g.:

    LLAMA_CPP_HOST=http://localhost:8080 python3 -m pytest tests/integration/test_llama_cpp_live.py

`llama-server` should be started with a tool-capable model in compat mode:

    llama-server -m models/llama-3.1-8b-instruct.gguf --port 8080
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
    not os.environ.get("LLAMA_CPP_HOST"),
    reason="LLAMA_CPP_HOST not set",
)


def _base_url() -> str:
    host = os.environ["LLAMA_CPP_HOST"].rstrip("/")
    if host.endswith("/v1"):
        return host
    return f"{host}/v1"


def _cfg(*, mode: str):
    from arc.config import ProviderConfig, RetryConfig
    return ProviderConfig(
        name="llama_cpp",
        model=os.environ.get("LLAMA_CPP_MODEL", ""),
        api_key_env="LLAMA_CPP_API_KEY",
        base_url=_base_url(),
        timeout_seconds=300.0,
        retry=RetryConfig(max_attempts=2, backoff_base_seconds=1, backoff_max_seconds=4),
        params={"mode": mode, "temperature": 0, "max_tokens": 64},
    )


def test_llama_cpp_compat_simple_chat():
    from arc.providers.llama_cpp import LlamaCppProvider
    from arc.runtime.hooks import LLMRequest, Message

    provider = LlamaCppProvider(_cfg(mode="compat"))
    resp = provider.chat(LLMRequest(
        messages=[Message(role="user", content="say the single word 'pong'")],
        system="reply with one word.",
        tools=[],
        model="",
        params={"temperature": 0, "max_tokens": 8},
    ))
    text_blocks = [b for b in resp.content if b.type == "text"]
    assert text_blocks
    assert resp.stop_reason in ("end_turn", "max_tokens", "other")


def test_llama_cpp_grammar_tool_call():
    from arc.providers.llama_cpp import LlamaCppProvider
    from arc.runtime.hooks import LLMRequest, Message, ToolSpec

    provider = LlamaCppProvider(_cfg(mode="grammar"))
    resp = provider.chat(LLMRequest(
        messages=[Message(role="user", content="list the files in /tmp using ls")],
        system="use the ls tool.",
        tools=[ToolSpec(
            name="ls",
            description="List files in a directory.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )],
        model="",
        params={"mode": "grammar", "temperature": 0, "max_tokens": 64},
    ))
    tool_calls = [b for b in resp.content if b.type == "tool_use"]
    assert tool_calls
    assert tool_calls[0].tool_name == "ls"
    assert "path" in (tool_calls[0].tool_input or {})
    # Mode telemetry made it into .raw
    assert resp.raw["_arc_llama_cpp"]["mode"] == "grammar"
