"""Unit tests for OllamaProvider.

The translation layer is exercised in test_openai_compat.py.  This file
covers Ollama-specific shape: default base_url, capability detection by
model name, preflight warn-on-missing behavior.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from arc.config import ProviderConfig, RetryConfig
from arc.providers import build
from arc.providers.ollama import DEFAULT_BASE_URL, OllamaProvider, _capabilities_for


def _cfg(**overrides) -> ProviderConfig:
    base = dict(
        name="ollama",
        model="llama3.1:8b",
        api_key_env="OLLAMA_API_KEY",
        base_url=None,
        timeout_seconds=120.0,
        retry=RetryConfig(max_attempts=3, backoff_base_seconds=0.0, backoff_max_seconds=0.0),
        params={"temperature": 0, "max_tokens": 256},
    )
    base.update(overrides)
    return ProviderConfig(**base)


def _stub_tags_response(models: list[str]):
    body = json.dumps({"models": [{"name": n} for n in models]}).encode("utf-8")
    resp = MagicMock()
    resp.read = MagicMock(return_value=body)
    resp.__enter__ = lambda self: resp
    resp.__exit__ = lambda *a: None
    return resp


# ── Factory wiring ─────────────────────────────────────────────────────────


def test_build_returns_ollama_for_known_name():
    with patch("openai.OpenAI"), \
         patch("arc.providers.ollama._preflight"):
        provider = build(_cfg())
    assert isinstance(provider, OllamaProvider)
    assert provider.name == "ollama"


def test_default_base_url_when_not_configured():
    with patch("openai.OpenAI") as mock_cls, \
         patch("arc.providers.ollama._preflight"):
        OllamaProvider(_cfg())
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["base_url"] == DEFAULT_BASE_URL


def test_explicit_base_url_is_respected():
    with patch("openai.OpenAI") as mock_cls, \
         patch("arc.providers.ollama._preflight"):
        OllamaProvider(_cfg(base_url="http://gpu-rig.lan:11434/v1"))
    assert mock_cls.call_args.kwargs["base_url"] == "http://gpu-rig.lan:11434/v1"


def test_api_key_env_falls_back_to_placeholder_when_unset(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    with patch("openai.OpenAI") as mock_cls, \
         patch("arc.providers.ollama._preflight"):
        OllamaProvider(_cfg())
    assert mock_cls.call_args.kwargs["api_key"] == "ollama"


def test_api_key_env_used_when_set(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "secret")
    with patch("openai.OpenAI") as mock_cls, \
         patch("arc.providers.ollama._preflight"):
        OllamaProvider(_cfg())
    assert mock_cls.call_args.kwargs["api_key"] == "secret"


# ── Capability detection ───────────────────────────────────────────────────


@pytest.mark.parametrize("model,expected_tools", [
    ("llama3.1:8b", True),
    ("llama3.2:3b", True),
    ("llama3.3:70b", True),
    ("hermes3:8b", True),
    ("qwen2.5:14b", True),
    ("mistral-nemo", True),
    ("command-r:35b", True),
    ("firefunction-v2", True),
    ("granite3.1-dense", True),
    # Older / non-tool families
    ("llama2:13b", False),
    ("phi3:3.8b", False),
    ("gemma2:9b", False),
    ("dolphin-mistral", False),
])
def test_capabilities_for_known_families(model, expected_tools):
    caps = _capabilities_for(model)
    assert caps.tool_use is expected_tools
    assert caps.parallel_tool_calls is expected_tools


def test_unknown_model_defaults_to_no_tools():
    caps = _capabilities_for("completely-novel-model:42b")
    assert caps.tool_use is False


# ── Preflight ──────────────────────────────────────────────────────────────


def test_preflight_warns_when_model_not_pulled(monkeypatch, caplog):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    with patch("openai.OpenAI"), \
         patch("arc.providers.ollama.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _stub_tags_response(["llama3.2:3b", "qwen2.5:14b"])
        with caplog.at_level("WARNING", logger="arc.providers.ollama"):
            OllamaProvider(_cfg(model="llama3.1:8b"))

    assert any("llama3.1:8b" in rec.message and "not in local cache" in rec.message
               for rec in caplog.records)


def test_preflight_silent_when_model_is_pulled(monkeypatch, caplog):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    with patch("openai.OpenAI"), \
         patch("arc.providers.ollama.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _stub_tags_response(["llama3.1:8b"])
        with caplog.at_level("WARNING", logger="arc.providers.ollama"):
            OllamaProvider(_cfg(model="llama3.1:8b"))
    # No warnings emitted
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == []


def test_preflight_accepts_latest_suffix_match(monkeypatch, caplog):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    with patch("openai.OpenAI"), \
         patch("arc.providers.ollama.urlopen") as mock_urlopen:
        # Server reports the canonical "llama3.1:latest"; config says "llama3.1"
        mock_urlopen.return_value = _stub_tags_response(["llama3.1:latest"])
        with caplog.at_level("WARNING", logger="arc.providers.ollama"):
            OllamaProvider(_cfg(model="llama3.1"))
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def test_preflight_swallows_unreachable_server(monkeypatch):
    from urllib.error import URLError
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    with patch("openai.OpenAI"), \
         patch("arc.providers.ollama.urlopen", side_effect=URLError("unreachable")):
        # Should not raise — preflight is warn-only and silent on connection errors
        OllamaProvider(_cfg())


def test_preflight_swallows_malformed_response(monkeypatch, caplog):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    bad = MagicMock()
    bad.read = MagicMock(return_value=b"not json")
    bad.__enter__ = lambda self: bad
    bad.__exit__ = lambda *a: None

    with patch("openai.OpenAI"), \
         patch("arc.providers.ollama.urlopen", return_value=bad):
        OllamaProvider(_cfg())  # should not raise


def test_preflight_strips_v1_suffix_for_tags_endpoint(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    captured = {}

    def _grab(req, timeout):
        captured["url"] = req.full_url
        return _stub_tags_response(["llama3.1:8b"])

    with patch("openai.OpenAI"), \
         patch("arc.providers.ollama.urlopen", side_effect=_grab):
        OllamaProvider(_cfg(base_url="http://localhost:11434/v1", model="llama3.1:8b"))

    assert captured["url"] == "http://localhost:11434/api/tags"
