"""Programmatic add/remove of MCP servers in config.yml (0025 follow-on)."""
from __future__ import annotations

import pytest
import yaml

from arc.bootstrap import bootstrap
from arc.mcp.config import McpConfigError, parse_mcp_config
from arc.setup.writer import write_mcp_server_add, write_mcp_server_remove


@pytest.fixture
def cfg_path(tmp_path):
    home = tmp_path / "home"
    bootstrap(home)
    return home / "config.yml"


def _servers(cfg_path):
    raw = yaml.safe_load(cfg_path.read_text())
    for e in raw["plugins"]["enabled"]:
        if e.get("name") == "mcp":
            return (e.get("config") or {}).get("servers") or []
    return None


def test_add_http_server(cfg_path):
    write_mcp_server_add(cfg_path, name="container", transport="http",
                         url="http://127.0.0.1:8770/mcp", tool_prefix="c")
    # config still parses via the plugin path
    parse_mcp_config({"servers": [dict(s) for s in _servers(cfg_path)]})
    srv = {s["name"]: s for s in _servers(cfg_path)}
    assert srv["container"]["url"].endswith("/mcp")
    assert srv["container"]["enabled"] is True


def test_add_stdio_server_with_env(cfg_path):
    write_mcp_server_add(cfg_path, name="prox", transport="stdio",
                         command=["uvx", "prox-mcp"], env={"K": "v"},
                         tools_allow=["a", "b"])
    srv = {s["name"]: s for s in _servers(cfg_path)}["prox"]
    assert srv["command"] == ["uvx", "prox-mcp"]
    assert srv["env"] == {"K": "v"}
    assert srv["tools_allow"] == ["a", "b"]


def test_add_creates_mcp_entry_when_missing(cfg_path):
    # Strip the mcp plugin entry (older config that predates it).
    raw = yaml.safe_load(cfg_path.read_text())
    raw["plugins"]["enabled"] = [e for e in raw["plugins"]["enabled"] if e.get("name") != "mcp"]
    cfg_path.write_text(yaml.safe_dump(raw))
    assert _servers(cfg_path) is None  # no mcp entry

    changes = write_mcp_server_add(cfg_path, name="s", transport="http", url="http://x")
    assert any("plugins.enabled[mcp]" == c.key for c in changes)  # entry created
    assert [s["name"] for s in _servers(cfg_path)] == ["s"]


def test_add_upsert_updates_existing(cfg_path):
    write_mcp_server_add(cfg_path, name="c", transport="http", url="http://old")
    write_mcp_server_add(cfg_path, name="c", transport="http", url="http://new")
    srv = _servers(cfg_path)
    assert len(srv) == 1 and srv[0]["url"] == "http://new"


def test_add_validation_http_requires_url(cfg_path):
    with pytest.raises(McpConfigError):
        write_mcp_server_add(cfg_path, name="bad", transport="http")


def test_add_validation_stdio_requires_command(cfg_path):
    with pytest.raises(McpConfigError):
        write_mcp_server_add(cfg_path, name="bad", transport="stdio")


def test_remove_server(cfg_path):
    write_mcp_server_add(cfg_path, name="a", transport="http", url="http://a")
    write_mcp_server_add(cfg_path, name="b", transport="http", url="http://b")
    write_mcp_server_remove(cfg_path, name="a")
    assert [s["name"] for s in _servers(cfg_path)] == ["b"]


def test_remove_missing_raises(cfg_path):
    with pytest.raises(ValueError):
        write_mcp_server_remove(cfg_path, name="ghost")


def test_comments_preserved_on_add(cfg_path):
    before = cfg_path.read_text().count("#")
    write_mcp_server_add(cfg_path, name="c", transport="http", url="http://x")
    assert cfg_path.read_text().count("#") == before


def test_cli_add_and_remove_round_trip(tmp_path):
    from arc.cli import main

    home = tmp_path / "h"
    bootstrap(home)
    rc = main(["--home", str(home), "mcp", "add", "svc",
               "--transport", "http", "--url", "http://127.0.0.1:1/mcp"])
    assert rc == 0
    assert "svc" in {s["name"] for s in _servers(home / "config.yml")}
    rc = main(["--home", str(home), "mcp", "remove", "svc"])
    assert rc == 0
    assert "svc" not in {s["name"] for s in _servers(home / "config.yml")}
