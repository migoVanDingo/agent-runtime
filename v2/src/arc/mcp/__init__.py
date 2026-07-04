"""MCP client integration (0025).

arc consumes external MCP servers and surfaces their tools into the registry as
first-class, observable, gated arc tools. Implemented as the built-in `mcp`
plugin (see `arc/plugins/__init__.py::_build_mcp` → `arc.mcp.bridge`), so it
inherits discovery, config, quarantine, bus binding, and the `arc plugins` menu.

Layers:
  config.py   McpConfig / McpServerConfig — parse the plugin's config block
  manager.py  connection lifecycle on a background asyncio loop; per-server
              isolation; discovery; sync bridges for arc's synchronous runtime
  transport.py stdio + streamable-HTTP client factories over the `mcp` SDK
  adapter.py  an MCP tool def -> an arc Tool (call routing + event emission)
  bridge.py   the built-in plugin: connect on session start, provide tools,
              disconnect on session end
"""
