"""`arc plugins` — interactive plugin management menu.

Shows every plugin arc knows about:

  - Built-in plugins (ship with arc)
  - Discovered out-of-tree plugins (pip-installed packages registering
    via the `arc.plugins` entry-point group)
  - Dangling config entries (listed in config.yml but the package is no
    longer installed — likely uninstalled but not cleaned up)

User can toggle enable/disable for any plugin and remove dangling entries.
Changes are persisted to config.yml via the comment-preserving writer.
Take effect on the next `arc` session — we don't hot-swap a running plugin.

This is the always-available escape hatch from the first-run prompt. If the
user said "no" to a plugin on first install, they come here to flip it back.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from arc.config import PluginsConfig, load
from arc.plugins import builtin_plugin_names, last_discovery
from arc.setup.writer import (
    WriteChange,
    remove_plugin_entry,
    render_changes,
    write_plugin_enablement,
)


@dataclass(frozen=True)
class PluginRow:
    """One row in the picker. Captures everything the renderer needs."""
    name: str
    kind: str          # "builtin" | "discovered" | "dangling"
    package: str | None
    version: str | None
    enabled: bool       # current state in config.yml (False for dangling)
    in_config: bool     # has an entry in config.yml at all

    @property
    def display(self) -> str:
        tag = {
            "builtin": "built-in",
            "discovered": f"{self.package} v{self.version}" if self.package else "external",
            "dangling": "not installed",
        }[self.kind]
        marker = " [!]" if self.kind == "dangling" else ""
        return f"{self.name:<28} {tag}{marker}"


def collect_rows(config_path: Path) -> list[PluginRow]:
    """Build the table of plugins, joining discovery state with config state.

    Sort: built-ins first (alpha), then discovered (alpha), then dangling.
    """
    cfg = load(config_path)
    in_config = {entry.name: entry for entry in cfg.plugins.enabled}
    builtins = builtin_plugin_names()
    report = last_discovery()
    discovered = report.by_name() if report is not None else {}

    rows: list[PluginRow] = []

    for name in sorted(builtins):
        entry = in_config.get(name)
        rows.append(PluginRow(
            name=name, kind="builtin",
            package=None, version=None,
            enabled=bool(entry.enabled) if entry else True,
            in_config=entry is not None,
        ))

    for name in sorted(discovered):
        if name in builtins:
            continue  # built-in shadowing already reported in discovery
        d = discovered[name]
        entry = in_config.get(name)
        rows.append(PluginRow(
            name=name, kind="discovered",
            package=d.package, version=d.package_version,
            enabled=bool(entry.enabled) if entry else False,
            in_config=entry is not None,
        ))

    for name, entry in sorted(in_config.items()):
        if name in builtins or name in discovered:
            continue
        rows.append(PluginRow(
            name=name, kind="dangling",
            package=None, version=None,
            enabled=bool(entry.enabled),
            in_config=True,
        ))

    return rows


def list_plugins(config_path: Path) -> int:
    """Non-interactive: print one row per plugin. Suitable for piping or CI."""
    rows = collect_rows(config_path)
    if not rows:
        sys.stdout.write("(no plugins discovered)\n")
        return 0
    sys.stdout.write(f"{'STATUS':<8}  {'NAME':<28}  KIND / PACKAGE\n")
    for r in rows:
        flag = "[on]" if r.enabled else "[off]"
        sys.stdout.write(f"{flag:<8}  {r.display}\n")
    return 0


def run_menu(config_path: Path) -> int:
    """Interactive checkbox menu. Returns 0 on success, non-zero on abort."""
    from prompt_toolkit.shortcuts import checkboxlist_dialog

    from arc.tui.themes import active as _active_theme

    rows = collect_rows(config_path)
    if not rows:
        sys.stdout.write("(no plugins discovered)\n")
        return 0

    # Filter out dangling rows from the toggle list; they get a separate
    # "clean up dangling entries?" prompt at the end.
    toggleable = [r for r in rows if r.kind != "dangling"]
    dangling = [r for r in rows if r.kind == "dangling"]

    if toggleable:
        values = [(r.name, r.display) for r in toggleable]
        default = [r.name for r in toggleable if r.enabled]

        selected = checkboxlist_dialog(
            title="arc plugins",
            text="Space to toggle, Enter to confirm. Built-ins can be disabled too.",
            values=values,
            default_values=default,
            style=_active_theme().pt_style,
        ).run()

        if selected is None:
            sys.stdout.write("(cancelled — no changes)\n")
            return 1

        selected_set = set(selected)
        all_changes: list[WriteChange] = []
        for r in toggleable:
            desired = r.name in selected_set
            if desired != r.enabled or not r.in_config:
                changes = write_plugin_enablement(
                    config_path, name=r.name, enabled=desired,
                )
                all_changes.extend(changes)

        if all_changes:
            sys.stdout.write("updated config.yml:\n")
            sys.stdout.write(render_changes(all_changes) + "\n")
        else:
            sys.stdout.write("(no changes)\n")

    if dangling:
        sys.stdout.write("\nDangling entries (in config.yml, package not installed):\n")
        for r in dangling:
            sys.stdout.write(f"  - {r.name}\n")
        sys.stdout.write("Remove these from config.yml? [y/N] ")
        sys.stdout.flush()
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer in ("y", "yes"):
            for r in dangling:
                remove_plugin_entry(config_path, name=r.name)
            sys.stdout.write(f"removed {len(dangling)} dangling entries\n")

    sys.stdout.write("\nChanges take effect on the next `arc` session.\n")
    return 0


__all__ = ["PluginRow", "collect_rows", "list_plugins", "run_menu"]
