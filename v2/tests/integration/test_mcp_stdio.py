"""End-to-end: the MCP manager against a real stdio MCP server (0025).

Spawns a tiny FastMCP echo server as a subprocess and drives it through the
manager's actor/async-bridge — the same path a configured stdio server takes.
Skips if the `mcp` SDK isn't installed.
"""
from __future__ import annotations

import sys
import textwrap

import pytest

pytest.importorskip("mcp")

from arc.mcp.adapter import McpTool  # noqa: E402
from arc.mcp.config import parse_mcp_config  # noqa: E402
from arc.mcp.manager import CONNECTED, McpManager  # noqa: E402

_SERVER = textwrap.dedent('''
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("echo")

    @mcp.tool()
    def echo(msg: str) -> str:
        "Echo the message back."
        return f"echo: {msg}"

    @mcp.tool()
    def boom() -> str:
        "Always raises."
        raise RuntimeError("kaboom")

    if __name__ == "__main__":
        mcp.run()
''')


class _RecordingBus:
    def __init__(self):
        self.types = []

    def emit(self, event):
        self.types.append(event.type)


@pytest.fixture
def server_path(tmp_path):
    p = tmp_path / "echo_server.py"
    p.write_text(_SERVER)
    return p


@pytest.fixture
def manager(server_path):
    cfg = parse_mcp_config({"servers": [{
        "name": "echo", "transport": "stdio",
        "command": [sys.executable, str(server_path)], "tool_prefix": "echo",
    }]})
    bus = _RecordingBus()
    mgr = McpManager(cfg, bus=bus)
    mgr.connect_all(connect_timeout=30.0)
    yield mgr, bus
    mgr.disconnect_all()


def test_connect_and_discover(manager):
    mgr, bus = manager
    names = {t.name for t in mgr.all_tools()}
    assert {"echo", "boom"} <= names
    status = mgr.status()[0]
    assert status["state"] == CONNECTED and status["tool_count"] == 2
    assert "mcp.server.connected" in bus.types
    assert "mcp.tools.discovered" in bus.types


def test_call_tool_success(manager):
    mgr, bus = manager
    out = mgr.call_tool("echo", "echo", {"msg": "hi"})
    assert "echo: hi" in out["text"] and out["is_error"] is False
    assert "mcp.tool.called" in bus.types and "mcp.tool.result" in bus.types


def test_call_tool_server_error_flagged(manager):
    mgr, _ = manager
    out = mgr.call_tool("echo", "boom", {})
    # FastMCP maps a raised exception to an isError result, not a transport failure.
    assert out["is_error"] is True


def test_adapter_execute_end_to_end(manager):
    mgr, _ = manager
    tool = next(McpTool(td, mgr, "echo") for td in mgr.all_tools() if td.name == "echo")
    assert "echo: yo" in tool.execute({"msg": "yo"})


def test_full_builder_bridge_path(server_path):
    """The real arc path: _build_mcp(config, ctx) -> bridge lifecycle -> tools."""
    from types import SimpleNamespace

    from arc.plugins import _build_mcp

    config = {"servers": [{
        "name": "echo", "transport": "stdio",
        "command": [sys.executable, str(server_path)], "tool_prefix": "echo",
    }]}
    bridge = _build_mcp(config, SimpleNamespace(bus=None))
    bridge.on_session_start(ctx=None)
    try:
        tools = {t.name: t for t in bridge.provides_tools()}
        assert "echo_echo" in tools
        assert "echo: via-bridge" in tools["echo_echo"].execute({"msg": "via-bridge"})
    finally:
        bridge.on_session_end(ctx=None)
