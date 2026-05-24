"""Transient-error classification + runner internal-retry behavior."""
from __future__ import annotations

from arc.runtime.subagents.guards import classify_error


# ── classify_error ────────────────────────────────────────────────────────


def test_classify_network_error_by_name():
    class ConnectError(Exception):
        pass
    assert classify_error(ConnectError("kaboom")) == "network"


def test_classify_read_timeout_by_name():
    class ReadTimeout(Exception):
        pass
    assert classify_error(ReadTimeout("slow")) == "timeout_transient"


def test_classify_http_429():
    class HTTPStatusError(Exception):
        pass
    err = HTTPStatusError("rate limited")
    err.response = type("R", (), {"status_code": 429})()
    assert classify_error(err) == "rate_limit"


def test_classify_http_500():
    class HTTPStatusError(Exception):
        pass
    err = HTTPStatusError("server died")
    err.response = type("R", (), {"status_code": 503})()
    assert classify_error(err) == "server_error"


def test_classify_anthropic_rate_limit_class():
    class RateLimitError(Exception):
        pass
    assert classify_error(RateLimitError("slow down")) == "rate_limit"


def test_unknown_error_is_logical():
    class WeirdError(Exception):
        pass
    assert classify_error(WeirdError("???")) is None


def test_logical_error_not_classified_as_transient():
    """ValueError, TypeError, KeyError — never retried internally."""
    assert classify_error(ValueError("bad arg")) is None
    assert classify_error(TypeError("wrong type")) is None
    assert classify_error(KeyError("missing")) is None
