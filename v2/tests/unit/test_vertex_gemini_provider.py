"""VertexGeminiProvider unit tests.

Mocks `google.genai.Client` so tests run without GCP credentials. Coverage:
- client construction validates project_id
- auto-attach detects gs:// URIs from gcs_stat-shaped tool results
- error mapping for 403 / 429
- response translation round-trips through the shared module
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from arc.config import ProviderConfig, RetryConfig
from arc.providers._gemini_translation import (
    append_file_data_to_last_user_message,
    find_auto_attach_file,
)
from arc.runtime.hooks import ContentBlock, LLMRequest, Message


def _cfg(*, params: dict | None = None) -> ProviderConfig:
    return ProviderConfig(
        name="vertex_gemini",
        model="gemini-2.5-pro",
        api_key_env="",
        base_url=None,
        timeout_seconds=10.0,
        retry=RetryConfig(max_attempts=1, backoff_base_seconds=0.01, backoff_max_seconds=0.05),
        params=params if params is not None else {"project_id": "my-proj", "region": "us-central1"},
    )


def _mock_response(text: str = "ok", finish_reason: str = "STOP"):
    """Mimic GenerateContentResponse just enough for translation."""
    resp = MagicMock()

    candidate = MagicMock()
    candidate.finish_reason = type("FR", (), {"value": finish_reason})()

    part = MagicMock()
    part.text = text
    part.function_call = None

    candidate.content.parts = [part]
    resp.candidates = [candidate]

    usage = MagicMock()
    usage.prompt_token_count = 10
    usage.candidates_token_count = 5
    resp.usage_metadata = usage

    resp.model_dump.return_value = {"fake": "raw"}
    return resp


# ── Construction ──────────────────────────────────────────────────────────


@patch("google.genai.Client")
def test_requires_project_id(mock_client_cls):
    from arc.providers.vertex_gemini import VertexGeminiProvider
    with pytest.raises(ValueError, match="requires params.project_id"):
        VertexGeminiProvider(_cfg(params={}))


@patch("google.genai.Client")
def test_accepts_vertex_project_id_alias(mock_client_cls):
    from arc.providers.vertex_gemini import VertexGeminiProvider
    p = VertexGeminiProvider(_cfg(params={"vertex_project_id": "p2", "vertex_region": "us-east1"}))
    assert p._project == "p2"
    assert p._region == "us-east1"


@patch("google.genai.Client")
def test_default_region(mock_client_cls):
    from arc.providers.vertex_gemini import VertexGeminiProvider
    p = VertexGeminiProvider(_cfg(params={"project_id": "p"}))
    assert p._region == "us-central1"


@patch("google.genai.Client")
def test_client_init_failure_includes_hints(mock_client_cls):
    from arc.providers.vertex_gemini import VertexGeminiProvider
    mock_client_cls.side_effect = Exception("PermissionDenied: missing role")
    with pytest.raises(RuntimeError, match="aiplatform"):
        VertexGeminiProvider(_cfg())


# ── chat() round-trip ────────────────────────────────────────────────────


@patch("google.genai.Client")
def test_chat_basic_text(mock_client_cls):
    from arc.providers.vertex_gemini import VertexGeminiProvider

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.return_value = _mock_response("hello back")

    p = VertexGeminiProvider(_cfg())
    req = LLMRequest(
        messages=[Message(role="user", content="hi")],
        system="you are helpful",
        tools=[],
        model="gemini-2.5-pro",
        params={},
    )
    resp = p.chat(req)
    assert resp.stop_reason == "end_turn"
    assert resp.input_tokens == 10
    assert resp.output_tokens == 5
    assert resp.content[0].text == "hello back"


# ── Auto-attach detection ────────────────────────────────────────────────


def test_find_auto_attach_video_from_gcs_stat_result():
    """A gcs_stat tool result with video content_type triggers attach."""
    stat_result = json.dumps({
        "uri": "gs://my-bucket/recordings/conf.mp4",
        "size_bytes": 1000000,
        "content_type": "video/mp4",
    })
    msgs = [
        Message(role="user", content="analyze the video"),
        Message(role="assistant", content=[
            ContentBlock(type="tool_use", tool_name="gcs_stat", tool_input={"uri": "..."}),
        ]),
        Message(role="tool", content=[{
            "function_response": {
                "name": "gcs_stat",
                "response": {"result": stat_result},
            },
        }], name="gcs_stat"),
    ]
    attach = find_auto_attach_file(msgs)
    assert attach == ("gs://my-bucket/recordings/conf.mp4", "video/mp4")


def test_find_auto_attach_ignores_text_results():
    """Non-media content types don't trigger auto-attach."""
    stat_result = json.dumps({
        "uri": "gs://my-bucket/docs/notes.txt",
        "content_type": "text/plain",
    })
    msgs = [Message(role="tool", content=[{
        "function_response": {"name": "gcs_stat", "response": {"result": stat_result}},
    }])]
    assert find_auto_attach_file(msgs) is None


def test_find_auto_attach_ignores_non_gcs_uris():
    stat_result = json.dumps({
        "uri": "https://example.com/foo.mp4",
        "content_type": "video/mp4",
    })
    msgs = [Message(role="tool", content=[{
        "function_response": {"name": "gcs_stat", "response": {"result": stat_result}},
    }])]
    assert find_auto_attach_file(msgs) is None


def test_find_auto_attach_returns_most_recent():
    """If multiple matching tool results, the last one wins."""
    first = json.dumps({"uri": "gs://b/first.mp4", "content_type": "video/mp4"})
    second = json.dumps({"uri": "gs://b/second.mp4", "content_type": "video/mp4"})
    msgs = [
        Message(role="tool", content=[{
            "function_response": {"name": "gcs_stat", "response": {"result": first}},
        }]),
        Message(role="tool", content=[{
            "function_response": {"name": "gcs_stat", "response": {"result": second}},
        }]),
    ]
    attach = find_auto_attach_file(msgs)
    assert attach == ("gs://b/second.mp4", "video/mp4")


def test_find_auto_attach_accepts_image_and_audio():
    for ct in ("image/png", "audio/mpeg"):
        result = json.dumps({"uri": f"gs://b/foo", "content_type": ct})
        msgs = [Message(role="tool", content=[{
            "function_response": {"name": "gcs_stat", "response": {"result": result}},
        }])]
        attach = find_auto_attach_file(msgs)
        assert attach is not None
        assert attach[1] == ct


def test_find_auto_attach_handles_unparseable_results():
    """Non-JSON tool results don't crash; they're just skipped."""
    msgs = [Message(role="tool", content=[{
        "function_response": {"name": "gcs_stat", "response": {"result": "not json"}},
    }])]
    assert find_auto_attach_file(msgs) is None


# ── append_file_data_to_last_user_message ────────────────────────────────


def test_append_file_data_to_user_message():
    contents = [
        {"role": "user", "parts": [{"text": "analyze this"}]},
        {"role": "model", "parts": [{"function_call": {"name": "gcs_stat", "args": {}}}]},
        {"role": "user", "parts": [{"function_response": {"name": "gcs_stat",
                                                            "response": {"result": "..."}}}]},
    ]
    append_file_data_to_last_user_message(contents, "gs://b/x.mp4", "video/mp4")
    # Last user message got an extra file_data part.
    last_user = contents[-1]
    assert last_user["role"] == "user"
    assert any("file_data" in p for p in last_user["parts"])
    file_part = next(p for p in last_user["parts"] if "file_data" in p)
    assert file_part["file_data"]["file_uri"] == "gs://b/x.mp4"
    assert file_part["file_data"]["mime_type"] == "video/mp4"


def test_append_file_data_creates_user_message_if_none():
    """If no user message exists, append a synthetic one."""
    contents = [{"role": "model", "parts": [{"text": "..."}]}]
    append_file_data_to_last_user_message(contents, "gs://b/x.mp4", "video/mp4")
    # A synthetic user message was added at the end.
    assert contents[-1]["role"] == "user"
    assert "file_data" in contents[-1]["parts"][0]


# ── Auto-attach end-to-end via chat() ────────────────────────────────────


@patch("google.genai.Client")
def test_chat_auto_attaches_video_from_gcs_stat(mock_client_cls):
    """chat() inserts a file_data part when messages include a gs:// video stat."""
    from arc.providers.vertex_gemini import VertexGeminiProvider

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.return_value = _mock_response("analyzed")

    p = VertexGeminiProvider(_cfg())

    stat_result = json.dumps({
        "uri": "gs://my-bucket/recordings/conf.mp4",
        "content_type": "video/mp4",
        "size_bytes": 5_000_000,
    })
    req = LLMRequest(
        messages=[
            Message(role="user", content="summarize the video"),
            Message(role="assistant", content=[
                ContentBlock(type="tool_use", tool_name="gcs_stat", tool_input={}),
            ]),
            Message(role="tool", content=[{
                "function_response": {"name": "gcs_stat",
                                       "response": {"result": stat_result}},
            }]),
        ],
        system="You analyze videos.",
        tools=[],
        model="gemini-2.5-pro",
        params={},
    )
    p.chat(req)

    # Inspect what was sent to Vertex.
    call_args = mock_client.models.generate_content.call_args
    contents = call_args.kwargs["contents"]
    # Find the file_data part anywhere in the contents
    found = False
    for msg in contents:
        for part in msg.get("parts", []):
            if "file_data" in part:
                fd = part["file_data"]
                assert fd["file_uri"] == "gs://my-bucket/recordings/conf.mp4"
                assert fd["mime_type"] == "video/mp4"
                found = True
    assert found, "expected file_data part in the request"


# ── Error mapping ────────────────────────────────────────────────────────


@patch("google.genai.Client")
def test_403_maps_to_clear_iam_error(mock_client_cls):
    from arc.providers.vertex_gemini import VertexGeminiProvider

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.side_effect = Exception("403 PermissionDenied")

    p = VertexGeminiProvider(_cfg())
    req = LLMRequest(messages=[Message(role="user", content="hi")], system="", tools=[],
                     model="gemini-2.5-pro", params={})
    with pytest.raises(RuntimeError, match="roles/aiplatform.user"):
        p.chat(req)


@patch("google.genai.Client")
def test_429_maps_to_clear_quota_error(mock_client_cls):
    from arc.providers.vertex_gemini import VertexGeminiProvider

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.side_effect = Exception("429 ResourceExhausted: quota")

    p = VertexGeminiProvider(_cfg())
    req = LLMRequest(messages=[Message(role="user", content="hi")], system="", tools=[],
                     model="gemini-2.5-pro", params={})
    with pytest.raises(RuntimeError, match="quota hit"):
        p.chat(req)
