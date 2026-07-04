"""MCP servers menu — the per-server enable/disable surface (0025).

Parity with plugin_menu.py, but the unit is a *server* nested in the built-in
`mcp` plugin's config block (see _deviations/0001), toggled via
`writer.write_mcp_server_enablement`.

Rows are config-level (name, transport, enabled, prefix) — NOT live connection
state, which would require connecting on every hub render (see _deviations/0002).
Use `arc mcp status` for a live probe.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class McpRow:
    name: str
    transport: str
    enabled: bool
    prefix: str

    @property
    def display(self) -> str:
        return f"{self.name}  ({self.transport}, → {self.prefix}_*)"


def _mcp_config_dict(config_path: Path) -> dict:
    """The `mcp` plugin's config block, or {} if not present."""
    import yaml

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    for entry in (raw.get("plugins") or {}).get("enabled") or []:
        if isinstance(entry, dict) and entry.get("name") == "mcp":
            return entry.get("config") or {}
    return {}


def collect_rows(config_path: Path) -> list[McpRow]:
    from arc.mcp.config import parse_mcp_config

    cfg = parse_mcp_config(_mcp_config_dict(config_path))
    return [McpRow(name=s.name, transport=s.transport, enabled=s.enabled, prefix=s.prefix)
            for s in cfg.servers]


def list_mcp(config_path: Path) -> int:
    rows = collect_rows(config_path)
    if not rows:
        sys.stdout.write("(no MCP servers configured under plugins.enabled[mcp].config.servers)\n")
        return 0
    enabled = sum(1 for r in rows if r.enabled)
    sys.stdout.write(f"MCP servers ({enabled} of {len(rows)} enabled):\n")
    for r in rows:
        mark = "●" if r.enabled else "○"
        sys.stdout.write(f"  {mark} {r.name:<20} {r.transport:<6} → {r.prefix}_*\n")
    return 0


def run_menu(config_path: Path) -> int:
    from prompt_toolkit.shortcuts import checkboxlist_dialog

    from arc.setup.writer import render_changes, write_mcp_server_enablement
    from arc.tui.themes import active as _active_theme

    rows = collect_rows(config_path)
    if not rows:
        sys.stdout.write(
            "(no MCP servers configured — add them under "
            "plugins.enabled[mcp].config.servers in config.yml)\n"
        )
        return 0

    selected = checkboxlist_dialog(
        title="arc MCP servers",
        text="Space to toggle, Enter to confirm. Takes effect next session.",
        values=[(r.name, r.display) for r in rows],
        default_values=[r.name for r in rows if r.enabled],
        style=_active_theme().pt_style,
    ).run()

    if selected is None:
        sys.stdout.write("(cancelled — no changes)\n")
        return 1

    selected_set = set(selected)
    all_changes = []
    for r in rows:
        desired = r.name in selected_set
        if desired != r.enabled:
            all_changes.extend(write_mcp_server_enablement(config_path, server=r.name, enabled=desired))

    if all_changes:
        sys.stdout.write("updated config.yml:\n")
        sys.stdout.write(render_changes(all_changes) + "\n")
    else:
        sys.stdout.write("(no changes)\n")
    return 0


__all__ = ["McpRow", "collect_rows", "list_mcp", "run_menu"]
