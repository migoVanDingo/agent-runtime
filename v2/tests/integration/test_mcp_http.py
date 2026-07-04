"""End-to-end: the MCP manager against a real streamable-HTTP MCP server (0025).

HTTP is the transport 0024's container-orchestration service will use, so it gets
its own live check. Spawns a FastMCP streamable-http server as a subprocess,
waits for the port, and drives it through the manager. Skips if `mcp` is absent.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
import time

import pytest

pytest.importorskip("mcp")

from arc.mcp.config import parse_mcp_config  # noqa: E402
from arc.mcp.manager import CONNECTED, McpManager  # noqa: E402

_PORT = 8791


def _server_src(port: int) -> str:
    return textwrap.dedent(f'''
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("echo", host="127.0.0.1", port={port})

        @mcp.tool()
        def echo(msg: str) -> str:
            "Echo the message back."
            return f"http-echo: {{msg}}"

        if __name__ == "__main__":
            mcp.run(transport="streamable-http")
    ''')


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture
def http_server(tmp_path):
    script = tmp_path / "http_server.py"
    script.write_text(_server_src(_PORT))
    proc = subprocess.Popen([sys.executable, str(script)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if _port_open(_PORT):
            break
        if proc.poll() is not None:
            pytest.skip("HTTP MCP server exited before binding")
        time.sleep(0.2)
    else:
        proc.terminate()
        pytest.skip("HTTP MCP server did not bind in time")
    yield f"http://127.0.0.1:{_PORT}/mcp"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_http_connect_and_call(http_server):
    cfg = parse_mcp_config({"servers": [{
        "name": "echo", "transport": "http", "url": http_server, "tool_prefix": "h"}]})
    mgr = McpManager(cfg)
    mgr.connect_all(connect_timeout=30.0)
    try:
        assert mgr.status()[0]["state"] == CONNECTED
        assert any(t.name == "echo" for t in mgr.all_tools())
        out = mgr.call_tool("echo", "echo", {"msg": "hey"})
        assert "http-echo: hey" in out["text"] and out["is_error"] is False
    finally:
        mgr.disconnect_all()
