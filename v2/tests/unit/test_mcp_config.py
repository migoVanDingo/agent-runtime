"""MCP plugin config parsing (0025)."""
from __future__ import annotations

import pytest

from arc.mcp.config import McpConfigError, parse_mcp_config


def test_empty_config_ok():
    cfg = parse_mcp_config(None)
    assert cfg.servers == ()
    assert cfg.active() == ()
    assert cfg.failure_threshold == 3


def test_http_server_parsed():
    cfg = parse_mcp_config({
        "servers": [
            {"name": "container", "transport": "http", "url": "http://127.0.0.1:8770/mcp",
             "tool_prefix": "c"},
        ],
    })
    s = cfg.servers[0]
    assert s.name == "container" and s.transport == "http" and s.url.endswith("/mcp")
    assert s.prefix == "c"


def test_stdio_server_parsed():
    cfg = parse_mcp_config({
        "servers": [{"name": "prox", "transport": "stdio", "command": ["uvx", "prox-mcp"]}],
    })
    s = cfg.servers[0]
    assert s.command == ("uvx", "prox-mcp")
    assert s.prefix == "prox"  # falls back to name


def test_active_filters_disabled():
    cfg = parse_mcp_config({
        "servers": [
            {"name": "a", "transport": "http", "url": "http://x", "enabled": False},
            {"name": "b", "transport": "http", "url": "http://y"},
        ],
    })
    assert [s.name for s in cfg.active()] == ["b"]


def test_bad_transport_rejected():
    with pytest.raises(McpConfigError):
        parse_mcp_config({"servers": [{"name": "a", "transport": "carrier-pigeon"}]})


def test_stdio_requires_command():
    with pytest.raises(McpConfigError):
        parse_mcp_config({"servers": [{"name": "a", "transport": "stdio"}]})


def test_http_requires_url():
    with pytest.raises(McpConfigError):
        parse_mcp_config({"servers": [{"name": "a", "transport": "http"}]})


def test_duplicate_names_rejected():
    with pytest.raises(McpConfigError):
        parse_mcp_config({"servers": [
            {"name": "a", "transport": "http", "url": "http://x"},
            {"name": "a", "transport": "http", "url": "http://y"},
        ]})


def test_missing_name_rejected():
    with pytest.raises(McpConfigError):
        parse_mcp_config({"servers": [{"transport": "http", "url": "http://x"}]})
