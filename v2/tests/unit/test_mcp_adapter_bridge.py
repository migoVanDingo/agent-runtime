"""MCP adapter + bridge against a fake manager — no SDK, no servers (0025)."""
from __future__ import annotations

import pytest

from arc.mcp.adapter import McpTool, _tool_name
from arc.mcp.bridge import McpBridge
from arc.mcp.config import parse_mcp_config
from arc.mcp.manager import McpCallError, ToolDef
from arc.tools.base import ToolError


class FakeManager:
    def __init__(self, tools, results=None):
        self._tools = tools
        self._results = results or {}
        self.calls = []
        self.disconnected = False
        self.raise_exc = None

    def connect_all(self):
        pass

    def all_tools(self):
        return list(self._tools)

    def call_tool(self, server, tool, arguments):
        self.calls.append((server, tool, arguments))
        if self.raise_exc:
            raise self.raise_exc
        return self._results.get(tool, {"text": "ok", "is_error": False, "structured": None})

    def disconnect_all(self):
        self.disconnected = True

    def status(self):
        return []


def _td(name="echo", server="srv", desc="Echo it", schema=None):
    return ToolDef(server=server, name=name,
                   description=desc, input_schema=schema or {"type": "object",
                   "properties": {"msg": {"type": "string"}}, "required": ["msg"]})


def test_tool_name_prefix_and_sanitize():
    assert _tool_name("container", "list files") == "container_list_files"
    assert _tool_name("", "plain") == "plain"
    assert len(_tool_name("p", "x" * 200)) == 64


def test_adapter_schema_and_execute():
    m = FakeManager(tools=[])
    tool = McpTool(_td(), m, prefix="container")
    assert tool.name == "container_echo"
    assert tool.input_schema.required == ["msg"]
    assert "msg" in tool.input_schema.properties
    m._results["echo"] = {"text": "hello", "is_error": False, "structured": None}
    assert tool.execute({"msg": "hello"}) == "hello"
    assert m.calls[-1] == ("srv", "echo", {"msg": "hello"})


def test_adapter_error_result_raises_tool_error():
    m = FakeManager(tools=[], results={"echo": {"text": "boom", "is_error": True, "structured": None}})
    tool = McpTool(_td(), m, prefix="c")
    with pytest.raises(ToolError):
        tool.execute({"msg": "x"})


def test_adapter_call_error_becomes_tool_error():
    m = FakeManager(tools=[])
    m.raise_exc = McpCallError("server not connected")
    tool = McpTool(_td(), m, prefix="c")
    with pytest.raises(ToolError):
        tool.execute({})


def test_bridge_lifecycle(monkeypatch):
    tools = [_td("a", "s1"), _td("b", "s1")]
    fake = FakeManager(tools=tools)
    import arc.mcp.bridge as bridge_mod
    monkeypatch.setattr(bridge_mod, "McpManager", lambda cfg, bus=None: fake)

    cfg = parse_mcp_config({"servers": [
        {"name": "s1", "transport": "http", "url": "http://x", "tool_prefix": "s1"}]})
    bridge = McpBridge(cfg)
    assert bridge.provides_tools() == []

    bridge.on_session_start(ctx=None)
    names = [t.name for t in bridge.provides_tools()]
    assert names == ["s1_a", "s1_b"]

    bridge.on_session_end(ctx=None)
    assert fake.disconnected is True
    assert bridge.provides_tools() == []


def test_bridge_drops_adapted_name_collision(monkeypatch):
    # Two tool names that sanitize to the same arc name — the bridge must drop
    # the dup, not let it crash session startup at merge_plugin_tools (M9).
    tools = [_td("a.b", "s1"), _td("a/b", "s1")]  # both → s1_a_b
    fake = FakeManager(tools=tools)
    import arc.mcp.bridge as bridge_mod
    monkeypatch.setattr(bridge_mod, "McpManager", lambda cfg, bus=None: fake)

    cfg = parse_mcp_config({"servers": [
        {"name": "s1", "transport": "http", "url": "http://x", "tool_prefix": "s1"}]})
    bridge = McpBridge(cfg)
    bridge.on_session_start(ctx=None)
    names = [t.name for t in bridge.provides_tools()]
    assert names == ["s1_a_b"]  # collision dropped, session not crashed
