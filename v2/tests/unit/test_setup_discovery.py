"""Unit tests for live model discovery used by `arc setup`."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from arc.setup.discovery import (
    DiscoveryResult,
    fetch_llama_cpp_models,
    fetch_ollama_models,
)


def _stub_httpx_get(payload: dict | None = None, *, status: int = 200,
                    raise_exc: Exception | None = None):
    def _get(url, timeout=5):
        if raise_exc:
            raise raise_exc
        resp = MagicMock()
        resp.status_code = status
        resp.json = MagicMock(return_value=payload or {})
        if status >= 400:
            resp.raise_for_status = MagicMock(
                side_effect=RuntimeError(f"HTTP {status}")
            )
        else:
            resp.raise_for_status = MagicMock()
        return resp
    return _get


# ── Ollama discovery ───────────────────────────────────────────────────────


def test_ollama_lists_pulled_models():
    payload = {
        "models": [
            {"name": "llama3.1:8b", "size": 4_700_000_000},
            {"name": "qwen2.5:14b", "size": 8_200_000_000},
            {"name": "llama3.2:1b", "size": 1_300_000_000},
        ]
    }
    with patch("httpx.get", side_effect=_stub_httpx_get(payload)):
        result = fetch_ollama_models("http://localhost:11434/v1")
    ids = [m.id for m in result.models]
    assert ids == ["llama3.1:8b", "qwen2.5:14b", "llama3.2:1b"]
    # Size + tools annotation present
    assert "GB" in result.models[0].note
    assert "tools ✓" in result.models[0].note  # llama3.1 is tool-capable


def test_ollama_strips_v1_for_tags_endpoint():
    captured = {}
    def _spy(url, timeout=5):
        captured["url"] = url
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value={"models": []})
        resp.raise_for_status = MagicMock()
        return resp
    with patch("httpx.get", side_effect=_spy):
        fetch_ollama_models("http://localhost:11434/v1")
    assert captured["url"] == "http://localhost:11434/api/tags"


def test_ollama_unreachable_returns_empty_with_reason():
    with patch("httpx.get", side_effect=ConnectionError("refused")):
        result = fetch_ollama_models("http://localhost:11434/v1")
    assert result.models == []
    assert "couldn't reach Ollama" in result.reason


def test_ollama_404_returns_empty_with_reason():
    with patch("httpx.get", side_effect=_stub_httpx_get(status=404)):
        result = fetch_ollama_models("http://localhost:11434/v1")
    assert result.models == []
    assert "couldn't reach Ollama" in result.reason


def test_ollama_empty_payload_returns_empty_list():
    with patch("httpx.get", side_effect=_stub_httpx_get({"models": []})):
        result = fetch_ollama_models("http://localhost:11434/v1")
    assert result.models == []


# ── llama.cpp discovery ────────────────────────────────────────────────────


def test_llama_cpp_lists_loaded_model():
    payload = {"data": [{"id": "Llama-3.1-8B-Instruct-Q4_K_M.gguf"}]}
    with patch("httpx.get", side_effect=_stub_httpx_get(payload)):
        result = fetch_llama_cpp_models("http://localhost:8080/v1")
    assert len(result.models) == 1
    assert result.models[0].id == "Llama-3.1-8B-Instruct-Q4_K_M.gguf"
    assert result.models[0].note == "loaded"


def test_llama_cpp_appends_v1_when_missing():
    captured = {}
    def _spy(url, timeout=5):
        captured["url"] = url
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value={"data": []})
        resp.raise_for_status = MagicMock()
        return resp
    with patch("httpx.get", side_effect=_spy):
        fetch_llama_cpp_models("http://localhost:8080")
    assert captured["url"] == "http://localhost:8080/v1/models"


def test_llama_cpp_unreachable_returns_empty():
    with patch("httpx.get", side_effect=ConnectionError("refused")):
        result = fetch_llama_cpp_models("http://localhost:8080/v1")
    assert result.models == []
    assert "couldn't reach llama-server" in result.reason
