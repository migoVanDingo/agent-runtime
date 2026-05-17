"""`arc plugin` subcommands — list, info, install, remove, reload, doctor.

The CLI runs discovery *without* registration so it never has to boot a full
Agent. That keeps `arc plugin list` fast and decoupled from any pipeline state.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from logger import get_logger
from plugins.deps import probe_dependencies
from plugins.loader import discover_plugins
from plugins.manifest import Manifest

logger = get_logger(__name__)


def _plugins_root() -> Path:
    from session_paths import arc_home
    return arc_home() / "plugins"


def _status_for(manifest: Manifest) -> tuple[str, str]:
    """Return (status, detail). status ∈ {'enabled', 'disabled'}."""
    missing = probe_dependencies(manifest.requires_python)
    if missing:
        detail = ", ".join(f"{m.requirement} ({m.reason})" for m in missing)
        return ("disabled", f"missing: {detail}")
    return ("enabled", "")


def _print_list() -> None:
    plugins = discover_plugins()
    if not plugins:
        print("No plugins installed.")
        print("")
        print("Install via:")
        print("    pip install <arc-plugin-pkg>     # PyPI / packaged plugin")
        print(f"    drop a .py file into {_plugins_root() / 'tools'}")
        return

    # Group by plugin name (multiple entries can share a manifest).
    by_name: dict[str, list] = {}
    for p in plugins:
        by_name.setdefault(p.name, []).append(p)

    enabled: list[tuple[str, str, list]] = []
    disabled: list[tuple[str, str, str]] = []
    for name, entries in by_name.items():
        manifest = entries[0].manifest
        status, detail = _status_for(manifest)
        if status == "enabled":
            enabled.append((name, manifest.version, entries))
        else:
            disabled.append((name, manifest.version, detail))

    if enabled:
        print(f"Enabled plugins ({len(enabled)}):")
        for name, version, entries in enabled:
            kinds = ", ".join(sorted({e.kind for e in entries}))
            sources = ", ".join(sorted({_short_source(e.source) for e in entries}))
            print(f"  {name} {version}    {len(entries)} entries ({kinds})   [{sources}]")
        print("")

    if disabled:
        print(f"Disabled plugins ({len(disabled)}):")
        for name, version, detail in disabled:
            print(f"  {name} {version}    {detail}")
        print("")


def _short_source(source: str) -> str:
    if source == "entry-point":
        return "entry-point"
    p = Path(source)
    return f"~/.arc/plugins/{p.parent.name}/{p.name}" if p.is_file() else f"~/.arc/plugins/{p.parent.name}/{p.name}/"


def _print_info(name: str) -> None:
    plugins = [p for p in discover_plugins() if p.name == name]
    if not plugins:
        print(f"plugin {name!r} not found")
        return
    manifest = plugins[0].manifest
    status, detail = _status_for(manifest)
    print(f"{manifest.name} {manifest.version}")
    if manifest.description:
        print(f"  {manifest.description}")
    if manifest.author:
        print(f"  Author:  {manifest.author}")
    print(f"  Status:  {status}{(' — ' + detail) if detail else ''}")
    print(f"  Sources: {', '.join(sorted({_short_source(p.source) for p in plugins}))}")
    tools = [p for p in plugins if p.kind == "tool"]
    skills = [p for p in plugins if p.kind == "skill"]
    toolsets = [p for p in plugins if p.kind == "toolset"]
    if tools:
        print(f"  Tools ({len(tools)}):")
        for t in tools:
            print(f"    {_short_source(t.source)}")
    if skills:
        print(f"  Skills ({len(skills)}):")
        for s in skills:
            print(f"    {_short_source(s.source)}")
    if toolsets:
        print(f"  Toolsets ({len(toolsets)}):")
        for ts in toolsets:
            print(f"    {_short_source(ts.source)}")
    if manifest.requires_python:
        print(f"  Requires (Python):")
        for req in manifest.requires_python:
            missing = probe_dependencies([req])
            mark = "✗" if missing else "✓"
            print(f"    {mark} {req}")
    if manifest.requires_system:
        print(f"  Requires (system, informational):")
        for req in manifest.requires_system:
            print(f"    • {req}")
    print(f"  Permissions:")
    print(f"    network={manifest.permissions.network}  filesystem_write={manifest.permissions.filesystem_write}")


def _print_doctor() -> None:
    from importlib import metadata
    print("Plugin doctor")
    print("─────────────")
    print("")
    print("Entry-point groups scanned:")
    for group in ("arc.tools", "arc.skills", "arc.toolsets"):
        try:
            eps = metadata.entry_points()
            count = len(eps.select(group=group)) if hasattr(eps, "select") else 0
            print(f"  {group:18s}  {count} entry point(s)")
        except Exception as exc:
            print(f"  {group:18s}  ERROR — {exc}")
    print("")
    root = _plugins_root()
    print(f"Filesystem root: {root}")
    print(f"  exists: {root.exists()}")
    if root.exists():
        for kind in ("tools", "skills"):
            sub = root / kind
            if sub.exists():
                contents = [c for c in sorted(sub.iterdir())
                            if c.name != "__pycache__" and not c.name.startswith(".")]
                print(f"  {kind}/: {len(contents)} entry(ies)")
                for c in contents:
                    print(f"    {c.name}")
            else:
                print(f"  {kind}/: (not present)")
    print("")
    plugins = discover_plugins()
    print(f"Total discovered plugins: {len(plugins)}")
    for p in plugins:
        status, detail = _status_for(p.manifest)
        print(f"  [{status}] {p.name} {p.manifest.version}  ({p.kind}, {_short_source(p.source)})")
        if detail:
            print(f"        {detail}")


def _install_filesystem(path: Path) -> None:
    """Copy a single .py file or directory plugin into ~/.arc/plugins/."""
    if not path.exists():
        print(f"path does not exist: {path}")
        sys.exit(1)
    # Determine kind by manifest if directory; require kind hint for .py files.
    root = _plugins_root()
    if path.is_file() and path.suffix == ".py":
        target_kind = _kind_hint_for_singlefile(path)
        dest_dir = root / target_kind
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / path.name
        shutil.copy2(path, dest)
        print(f"installed {path} → {dest}")
        return
    if path.is_dir():
        manifest_path = path / "plugin.toml"
        if not manifest_path.exists():
            print(f"{path} has no plugin.toml; refusing to install")
            sys.exit(1)
        from plugins.manifest import parse_toml_manifest
        manifest = parse_toml_manifest(manifest_path)
        target_kind = "tools" if manifest.entry.tools or manifest.entry.toolsets else "skills"
        dest_dir = root / target_kind / path.name
        if dest_dir.exists():
            print(f"target already exists: {dest_dir}")
            sys.exit(1)
        shutil.copytree(path, dest_dir)
        print(f"installed {path} → {dest_dir}")
        return
    print(f"unsupported plugin path: {path}")
    sys.exit(1)


def _kind_hint_for_singlefile(path: Path) -> str:
    """Pick `tools/` vs `skills/` for a single-file plugin.

    Prefers an ARC_PLUGIN extends_toolset hint or a `Skill` import; defaults to tools/.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if "from skills.base" in text or "import skills.base" in text:
        return "skills"
    return "tools"


def _install_package(name: str) -> None:
    """Pip-install a plugin package (uv preferred, fallback to pip)."""
    if " " in name or "/" in name or name.startswith("-"):
        print(f"refusing to pass suspicious package spec to pip: {name!r}")
        sys.exit(1)
    cmd_uv = ["uv", "pip", "install", name]
    cmd_pip = [sys.executable, "-m", "pip", "install", name]
    print(f"installing {name} …")
    try:
        if shutil.which("uv"):
            r = subprocess.run(cmd_uv, check=False)
            if r.returncode != 0:
                print("uv failed; falling back to pip")
                subprocess.run(cmd_pip, check=True)
        else:
            subprocess.run(cmd_pip, check=True)
        print(f"installed {name}. Restart arc to load the new plugin.")
    except subprocess.CalledProcessError as exc:
        print(f"install failed: {exc}")
        sys.exit(1)


def _remove(name: str) -> None:
    """Remove a filesystem plugin by name; for packages tell the user to uninstall."""
    root = _plugins_root()
    candidates: list[Path] = []
    for kind in ("tools", "skills"):
        sub = root / kind
        if not sub.exists():
            continue
        for entry in sub.iterdir():
            if entry.name == name or entry.stem == name:
                candidates.append(entry)
    if not candidates:
        print(f"no filesystem plugin found named {name!r}.")
        print(f"If installed as a package, run:  pip uninstall {name}")
        return
    for entry in candidates:
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
        print(f"removed {entry}")
    print(f"Restart arc to apply.")


def _reload() -> None:
    print("Plugin hot-reload is not implemented in v1.")
    print("Restart arc to load any newly installed or removed plugins.")


# ── argparse entry point ─────────────────────────────────────────────────────


def cmd_plugin(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="arc plugin")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="show installed plugins")
    p_info = sub.add_parser("info", help="show details for one plugin")
    p_info.add_argument("name")
    p_install = sub.add_parser("install", help="install a plugin (path or package name)")
    p_install.add_argument("target")
    p_remove = sub.add_parser("remove", help="remove a filesystem plugin")
    p_remove.add_argument("name")
    sub.add_parser("reload", help="(placeholder) ask to restart")
    sub.add_parser("doctor", help="diagnose plugin discovery")

    args = parser.parse_args(argv)
    if args.action == "list":
        _print_list()
    elif args.action == "info":
        _print_info(args.name)
    elif args.action == "install":
        target = args.target
        path = Path(target).expanduser()
        if path.exists():
            _install_filesystem(path)
        else:
            _install_package(target)
    elif args.action == "remove":
        _remove(args.name)
    elif args.action == "reload":
        _reload()
    elif args.action == "doctor":
        _print_doctor()
