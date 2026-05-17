"""Plugin manifest parsing and validation.

Manifest formats:

1. Directory plugins / packaged plugins → `plugin.toml`:
    [plugin]
    name = "arc-pdf-extras"
    version = "0.1.0"
    description = "..."
    arc_min_version = "0.3.0"

    [plugin.entry]               # only for filesystem dir plugins
    tools = ["module:ClassName", ...]
    skills = ["module:ClassName", ...]
    toolsets = ["MODULE_LEVEL_TOOLSET"]

    [plugin.requires]
    python = ["camelot-py>=0.11"]   # PEP 508 requirement strings
    system = ["poppler-utils"]      # informational only

    [plugin.permissions]
    network = false
    filesystem_write = false

2. Single-file plugins → module-level ``ARC_PLUGIN`` dict using the same keys
   (no [plugin] wrapper, no [plugin.entry] section — class discovery is
   automatic for single-file plugins).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


class ManifestError(ValueError):
    """Raised when a plugin manifest is missing required fields or malformed."""


@dataclass(frozen=True)
class PluginEntry:
    """Filesystem-plugin manifest entries pointing at concrete classes.

    Each string is ``"module_path:ClassName"`` resolved relative to the
    plugin's package import path.
    """

    tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    toolsets: tuple[str, ...] = ()


@dataclass(frozen=True)
class PluginPermissions:
    """Permissions block — consulted by the ActionGuard."""

    network: bool = False
    filesystem_write: bool = False


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    description: str = ""
    author: str = ""
    arc_min_version: str | None = None
    requires_python: tuple[str, ...] = ()
    requires_system: tuple[str, ...] = ()
    permissions: PluginPermissions = field(default_factory=PluginPermissions)
    entry: PluginEntry = field(default_factory=PluginEntry)
    extends_toolset: str | None = None  # single-file plugin convenience

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "arc_min_version": self.arc_min_version,
            "requires_python": list(self.requires_python),
            "requires_system": list(self.requires_system),
            "permissions": {
                "network": self.permissions.network,
                "filesystem_write": self.permissions.filesystem_write,
            },
            "entry": {
                "tools": list(self.entry.tools),
                "skills": list(self.entry.skills),
                "toolsets": list(self.entry.toolsets),
            },
            "extends_toolset": self.extends_toolset,
        }


# ── Parsing ──────────────────────────────────────────────────────────────────

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _coerce_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    raise ManifestError(f"expected list/string, got {type(value).__name__}")


def _build_manifest(raw: dict[str, Any]) -> Manifest:
    name = raw.get("name")
    if not name or not isinstance(name, str):
        raise ManifestError("manifest is missing required field 'name'")
    if not _NAME_RE.match(name):
        raise ManifestError(f"invalid plugin name {name!r} (use [A-Za-z0-9._-])")
    version = str(raw.get("version") or "0.0.0")

    requires_block = raw.get("requires") or {}
    if not isinstance(requires_block, dict):
        raise ManifestError("'requires' must be a table/dict")

    permissions_block = raw.get("permissions") or {}
    if not isinstance(permissions_block, dict):
        raise ManifestError("'permissions' must be a table/dict")
    permissions = PluginPermissions(
        network=bool(permissions_block.get("network", False)),
        filesystem_write=bool(permissions_block.get("filesystem_write", False)),
    )

    entry_block = raw.get("entry") or {}
    if not isinstance(entry_block, dict):
        raise ManifestError("'entry' must be a table/dict")
    entry = PluginEntry(
        tools=_coerce_tuple(entry_block.get("tools")),
        skills=_coerce_tuple(entry_block.get("skills")),
        toolsets=_coerce_tuple(entry_block.get("toolsets")),
    )

    return Manifest(
        name=name,
        version=version,
        description=str(raw.get("description") or ""),
        author=str(raw.get("author") or ""),
        arc_min_version=(str(raw["arc_min_version"]) if raw.get("arc_min_version") else None),
        requires_python=_coerce_tuple(requires_block.get("python")),
        requires_system=_coerce_tuple(requires_block.get("system")),
        permissions=permissions,
        entry=entry,
        extends_toolset=(str(raw["extends_toolset"]) if raw.get("extends_toolset") else None),
    )


def parse_toml_manifest(path: Path) -> Manifest:
    """Parse a plugin.toml file. The [plugin] table is the manifest root."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ManifestError(f"failed to parse {path}: {exc}") from exc
    root = data.get("plugin")
    if not isinstance(root, dict):
        raise ManifestError(f"{path}: missing [plugin] table")
    return _build_manifest(root)


def parse_dict_manifest(raw: dict[str, Any]) -> Manifest:
    """Parse a module-level ARC_PLUGIN dict (single-file filesystem plugins)."""
    if not isinstance(raw, dict):
        raise ManifestError("ARC_PLUGIN must be a dict")
    return _build_manifest(raw)


def synthesize_manifest(name: str, version: str = "0.0.0") -> Manifest:
    """Build a minimal manifest when none was provided (entry-point shortcut)."""
    return _build_manifest({"name": name, "version": version})
