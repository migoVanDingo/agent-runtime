"""Unit tests for the event payload redactor."""
import pytest
from runtime.events.redactor import RegexRedactor, get_redactor


r = RegexRedactor()


def test_scrubs_sk_api_key():
    payload = {"message": "key is sk-abcdefghijklmnopqrstuvwx123"}
    result = r.redact_payload(payload)
    assert "sk-" not in str(result)
    assert "<api_key>" in str(result)


def test_scrubs_anthropic_env_var():
    payload = {"cmd": "ANTHROPIC_API_KEY=sk-abc123 python run.py"}
    result = r.redact_payload(payload)
    assert "sk-abc123" not in str(result)


def test_scrubs_home_path():
    payload = {"path": "/Users/alice/project/data.txt"}
    result = r.redact_payload(payload)
    assert "/Users/alice/" not in str(result)
    assert "/Users/" not in str(result)


def test_scrubs_linux_home_path():
    payload = {"path": "/home/bob/workspace/file.py"}
    result = r.redact_payload(payload)
    assert "/home/bob" not in str(result)


def test_scrubs_email():
    payload = {"user": "alice@example.com"}
    result = r.redact_payload(payload)
    assert "@example.com" not in str(result)
    assert "<email>" in str(result)


def test_preserves_non_sensitive_fields():
    payload = {"tool_name": "read_file", "ok": True, "result_bytes": 1024}
    result = r.redact_payload(payload)
    assert result["tool_name"] == "read_file"
    assert result["ok"] is True
    assert result["result_bytes"] == 1024


def test_nested_secret_scrubbed():
    payload = {"inner": {"key": "sk-verysecretkeyvalue123456"}}
    result = r.redact_payload(payload)
    assert "sk-verysecretkeyvalue" not in str(result)


def test_roundtrip_non_secret_payload():
    payload = {"a": 1, "b": "hello", "c": [1, 2, 3]}
    result = r.redact_payload(payload)
    assert result == payload


def test_redact_event_marks_redacted_true():
    from runtime.events.schema import RuntimeEvent, EventPrivacy
    from runtime.identity import RuntimeIdentity
    identity = RuntimeIdentity.new_session(session_id="S1")
    event = RuntimeEvent(
        event_type="test",
        identity=identity,
        payload={"key": "sk-secret12345678"},
        privacy=EventPrivacy(redacted=False),
    )
    result = r.redact_event(event)
    assert result.privacy.redacted is True
    assert "sk-secret" not in str(result.payload)


def test_get_redactor_singleton():
    r1 = get_redactor()
    r2 = get_redactor()
    assert r1 is r2
