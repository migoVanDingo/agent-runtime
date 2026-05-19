"""Integration test for GeminiProvider — hits the real API.

Skipped if GEMINI_API_KEY is not set in the env. The model name is taken from
the default config so the test reflects what users actually run with.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _load_dotenv() -> None:
    """Minimal .env loader. Avoids the dotenv dep just for tests."""
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
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)


def test_gemini_simple_call():
    from arc.config import ProviderConfig, RetryConfig
    from arc.providers.gemini import GeminiProvider
    from arc.runtime.hooks import LLMRequest, Message

    cfg = ProviderConfig(
        name="gemini",
        model="gemini-3.1-flash-lite-preview",
        api_key_env="GEMINI_API_KEY",
        base_url=None,
        timeout_seconds=60.0,
        retry=RetryConfig(max_attempts=2, backoff_base_seconds=1.0, backoff_max_seconds=4.0),
        params={"temperature": 0, "max_tokens": 32},
    )

    provider = GeminiProvider(cfg)
    req = LLMRequest(
        messages=[Message(role="user", content="Reply with exactly: pong")],
        system="You are concise. Reply with exactly what's asked.",
        tools=[],
        model=cfg.model,
        params=cfg.params,
    )
    resp = provider.chat(req)

    # Some response with text in it
    text_blocks = [b for b in resp.content if b.type == "text"]
    assert len(text_blocks) >= 1
    assert "pong" in text_blocks[0].text.lower()

    # Token counts are populated
    assert resp.input_tokens > 0
    assert resp.output_tokens > 0

    # Raw response captured for replay
    assert isinstance(resp.raw, dict)
    assert "candidates" in resp.raw
