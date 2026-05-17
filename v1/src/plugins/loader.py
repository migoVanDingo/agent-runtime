"""Plugin discovery and registration.

Two-pass discovery: entry points first, filesystem second. Both produce
``DiscoveredPlugin`` records, which the loader walks to:

- Detect conflicts with built-in names (built-ins always win).
- Probe declared Python dependencies; disable plugins whose deps are missing.
- Instantiate and register the plugin's tools/skills/toolsets.
- Emit ``plugin.loaded`` / ``plugin.disabled`` / ``plugin.dep_missing``
  telemetry events (joins 0087 event schema).

Order of precedence for name collisions:

    built-in  >  entry-point plugin  >  filesystem plugin

within entry points and filesystem each, first-registered wins; conflicts
beyond the first are logged in the load report.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from logger import get_logger
from plugins.deps import MissingDep, probe_dependencies
from plugins.manifest import (
    Manifest,
    ManifestError,
    parse_dict_manifest,
    parse_toml_manifest,
    synthesize_manifest,
)

logger = get_logger(__name__)


# ── Records ──────────────────────────────────────────────────────────────────


@dataclass
class DiscoveredPlugin:
    """A plugin discovered but not yet validated or registered."""

    name: str                       # plugin display name (manifest name)
    kind: str                       # "tool" | "skill" | "toolset"
    source: str                     # "entry-point" | path to file/dir
    manifest: Manifest
    # Resolver: produces the live object on demand. Raises on failure.
    resolve: callable                # () -> Any (class or Toolset instance)

    def resolve_safely(self) -> tuple[Any, str | None]:
        """Return (obj, error). On failure, obj is None and error is set."""
        try:
            return self.resolve(), None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"


@dataclass
class LoadReport:
    enabled: list[str] = field(default_factory=list)
    disabled: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    conflicts: dict[str, str] = field(default_factory=dict)
    by_plugin: dict[str, Manifest] = field(default_factory=dict)

    def summary_line(self) -> str:
        return (
            f"plugins: {len(self.enabled)} enabled, "
            f"{len(self.disabled)} disabled, "
            f"{len(self.failed)} failed, "
            f"{len(self.conflicts)} conflicts"
        )


# ── Discovery: entry points ─────────────────────────────────────────────────


_GROUP_TOOLS = "arc.tools"
_GROUP_SKILLS = "arc.skills"
_GROUP_TOOLSETS = "arc.toolsets"


def _resolve_entry_point(ep):
    def _resolver():
        return ep.load()
    return _resolver


def _manifest_for_entry_point(ep) -> Manifest:
    """Try the distribution name as the manifest name; fall back to entry-point name."""
    dist_name: str | None = None
    version = "0.0.0"
    try:
        dist = ep.dist  # importlib.metadata: PathDistribution
        if dist is not None:
            dist_name = dist.metadata["Name"]
            version = dist.version or "0.0.0"
    except Exception:
        pass
    name = dist_name or f"entry:{ep.name}"
    return synthesize_manifest(name, version)


def _discover_entry_points() -> list[DiscoveredPlugin]:
    found: list[DiscoveredPlugin] = []
    try:
        eps = metadata.entry_points()
    except Exception as exc:
        logger.warning(f"plugins: entry-point discovery failed: {exc}")
        return found

    def _select(group: str):
        # importlib.metadata.entry_points API differs across versions.
        if hasattr(eps, "select"):
            return eps.select(group=group)
        return [e for e in eps.get(group, [])] if isinstance(eps, dict) else [
            e for e in eps if getattr(e, "group", "") == group
        ]

    for kind, group in (("tool", _GROUP_TOOLS), ("skill", _GROUP_SKILLS), ("toolset", _GROUP_TOOLSETS)):
        for ep in _select(group):
            manifest = _manifest_for_entry_point(ep)
            found.append(DiscoveredPlugin(
                name=manifest.name,
                kind=kind,
                source="entry-point",
                manifest=manifest,
                resolve=_resolve_entry_point(ep),
            ))
    return found


# ── Discovery: filesystem ────────────────────────────────────────────────────


def _plugins_root() -> Path:
    from session_paths import arc_home
    return arc_home() / "plugins"


def _safe_under(child: Path, parent: Path) -> bool:
    """Reject any plugin path that escapes ~/.arc/plugins/ via symlinks/..."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _load_module_from_path(name: str, path: Path):
    """Import a python file (or package __init__.py) under a synthetic module name."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ManifestError(f"cannot create module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _find_subclasses(module, base) -> list[type]:
    """Return classes in `module` (or its public symbols) that subclass `base`."""
    results: list[type] = []
    for attr in dir(module):
        if attr.startswith("_"):
            continue
        obj = getattr(module, attr, None)
        if isinstance(obj, type) and issubclass(obj, base) and obj is not base:
            results.append(obj)
    return results


def _resolve_module_attr(module, ref: str):
    """Resolve ``"submodule:Name"`` or ``"Name"`` against the loaded module."""
    if ":" in ref:
        _, _, attr = ref.partition(":")
    else:
        attr = ref
    obj = getattr(module, attr, None)
    if obj is None:
        raise ManifestError(f"{module.__name__} has no attribute {attr!r}")
    return obj


def _discover_single_file(path: Path, kind_dir: str) -> list[DiscoveredPlugin]:
    """Discover one ``~/.arc/plugins/tools/x.py`` or ``skills/x.py`` file."""
    from hashlib import sha1
    from skills.base import Skill
    from tools.base import BaseTool
    from tools.toolset import Toolset

    # Include a hash of the absolute path so two plugins with the same
    # filename in different ARC_HOMEs (test isolation, sandboxed installs)
    # don't collide in sys.modules.
    path_hash = sha1(str(path.resolve()).encode()).hexdigest()[:8]
    mod_name = f"arc_plugin_fs_{kind_dir}_{path.stem}_{path_hash}"
    found: list[DiscoveredPlugin] = []

    def _load_module():
        return _load_module_from_path(mod_name, path)

    # Peek the module without raising — needed for the manifest and for class scan.
    try:
        module = _load_module()
    except Exception as exc:
        logger.warning(f"plugins: failed to load {path}: {exc}")
        return found

    raw = getattr(module, "ARC_PLUGIN", None)
    try:
        manifest = parse_dict_manifest(raw) if raw else synthesize_manifest(
            f"fs-{path.stem}", "0.0.0",
        )
    except ManifestError as exc:
        logger.warning(f"plugins: bad manifest in {path}: {exc}")
        return found

    if kind_dir == "tools":
        for cls in _find_subclasses(module, BaseTool):
            found.append(DiscoveredPlugin(
                name=manifest.name,
                kind="tool",
                source=str(path),
                manifest=manifest,
                resolve=lambda cls=cls: cls,
            ))
        for attr in dir(module):
            obj = getattr(module, attr, None)
            if isinstance(obj, Toolset):
                found.append(DiscoveredPlugin(
                    name=manifest.name,
                    kind="toolset",
                    source=str(path),
                    manifest=manifest,
                    resolve=lambda obj=obj: obj,
                ))
    elif kind_dir == "skills":
        for cls in _find_subclasses(module, Skill):
            found.append(DiscoveredPlugin(
                name=manifest.name,
                kind="skill",
                source=str(path),
                manifest=manifest,
                resolve=lambda cls=cls: cls,
            ))
    return found


def _discover_dir_plugin(dir_path: Path, kind_dir: str) -> list[DiscoveredPlugin]:
    """Discover one ``~/.arc/plugins/tools/<name>/plugin.toml`` directory plugin."""
    manifest_path = dir_path / "plugin.toml"
    if not manifest_path.exists():
        return []
    try:
        manifest = parse_toml_manifest(manifest_path)
    except ManifestError as exc:
        logger.warning(f"plugins: bad manifest at {manifest_path}: {exc}")
        return []

    found: list[DiscoveredPlugin] = []
    # Ensure the parent of the plugin dir is on sys.path so its package imports work.
    parent = str(dir_path.parent.resolve())
    if parent not in sys.path:
        sys.path.insert(0, parent)

    pkg_name = dir_path.name

    def _resolver_for(ref: str, base_kind: str):
        def _resolve():
            module = importlib.import_module(pkg_name)
            obj = _resolve_module_attr(module, ref)
            return obj
        _resolve.__doc__ = f"{base_kind}:{ref}"
        return _resolve

    for ref in manifest.entry.tools:
        found.append(DiscoveredPlugin(
            name=manifest.name, kind="tool", source=str(dir_path), manifest=manifest,
            resolve=_resolver_for(ref, "tool"),
        ))
    for ref in manifest.entry.skills:
        found.append(DiscoveredPlugin(
            name=manifest.name, kind="skill", source=str(dir_path), manifest=manifest,
            resolve=_resolver_for(ref, "skill"),
        ))
    for ref in manifest.entry.toolsets:
        found.append(DiscoveredPlugin(
            name=manifest.name, kind="toolset", source=str(dir_path), manifest=manifest,
            resolve=_resolver_for(ref, "toolset"),
        ))
    return found


def _discover_filesystem() -> list[DiscoveredPlugin]:
    root = _plugins_root()
    if not root.exists():
        return []
    found: list[DiscoveredPlugin] = []
    for kind_dir in ("tools", "skills"):
        kind_root = root / kind_dir
        if not kind_root.exists():
            continue
        for entry in sorted(kind_root.iterdir()):
            if entry.name == "__pycache__" or entry.name.startswith("."):
                continue
            if not _safe_under(entry, root):
                logger.warning(f"plugins: rejecting path outside {root}: {entry}")
                continue
            if entry.is_file() and entry.suffix == ".py":
                found.extend(_discover_single_file(entry, kind_dir))
            elif entry.is_dir():
                found.extend(_discover_dir_plugin(entry, kind_dir))
    return found


def discover_plugins() -> list[DiscoveredPlugin]:
    """Run both discovery passes. Entry points appear before filesystem."""
    plugins: list[DiscoveredPlugin] = []
    plugins.extend(_discover_entry_points())
    plugins.extend(_discover_filesystem())
    return plugins


# ── Registration ─────────────────────────────────────────────────────────────


def _emit_plugin_event(event_type: str, plugin: DiscoveredPlugin, *, extra: dict | None = None) -> None:
    """Best-effort plugin telemetry. Never raise."""
    try:
        from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
        payload = {
            "plugin_name": plugin.name,
            "kind": plugin.kind,
            "source": plugin.source,
            "version": plugin.manifest.version,
        }
        if extra:
            payload.update(extra)
        get_event_bus().emit(RuntimeEvent(
            event_type,
            get_runtime_identity(),
            payload=payload,
            stage="PluginLoader",
            severity="info" if event_type == "plugin.loaded" else "warn",
        ))
    except Exception:
        pass


def _instantiate(obj: Any) -> Any:
    """Discovery returns classes (or Toolset instances). Instantiate classes."""
    from tools.toolset import Toolset
    if isinstance(obj, Toolset):
        return obj
    if isinstance(obj, type):
        return obj()
    return obj


def _register_one(
    plugin: DiscoveredPlugin,
    obj: Any,
    registry,
    skill_registry,
    builtin_tool_names: set[str],
    builtin_skill_names: set[str],
    report: LoadReport,
) -> bool:
    """Apply one resolved plugin object. Returns True on success."""
    from skills.base import Skill
    from tools.base import BaseTool
    from tools.toolset import Toolset

    instance = _instantiate(obj)

    if plugin.kind == "tool" or isinstance(instance, BaseTool):
        if not isinstance(instance, BaseTool):
            report.failed[plugin.name] = f"{plugin.kind} entry is not a BaseTool subclass"
            return False
        name = instance.name
        if name in builtin_tool_names:
            report.conflicts[plugin.name] = f"tool name {name!r} conflicts with built-in"
            return False
        if name in registry.tool_names():
            report.conflicts[plugin.name] = f"tool name {name!r} already registered"
            return False
        registry.register(instance)
        registry.record_plugin_manifest(name, plugin.manifest)
        # If the tool declares extends_toolset (class attr) or manifest does, join it.
        target_ts = getattr(instance, "extends_toolset", None) or plugin.manifest.extends_toolset
        if target_ts and target_ts in registry.toolset_names():
            registry.attach_tool_to_toolset(target_ts, instance)
        return True

    if plugin.kind == "skill" or isinstance(instance, Skill):
        if not isinstance(instance, Skill):
            report.failed[plugin.name] = f"{plugin.kind} entry is not a Skill subclass"
            return False
        if instance.name in builtin_skill_names:
            report.conflicts[plugin.name] = f"skill name {instance.name!r} conflicts with built-in"
            return False
        if instance.name in skill_registry.names():
            report.conflicts[plugin.name] = f"skill name {instance.name!r} already registered"
            return False
        skill_registry.register(instance)
        return True

    if plugin.kind == "toolset" or isinstance(instance, Toolset):
        if not isinstance(instance, Toolset):
            report.failed[plugin.name] = "toolset entry is not a Toolset instance"
            return False
        # Skip tools that collide with built-ins; ship the rest.
        plugin_tools = [t for t in instance.tools if t.name not in builtin_tool_names]
        if len(plugin_tools) != len(instance.tools):
            dropped = [t.name for t in instance.tools if t.name in builtin_tool_names]
            report.conflicts[plugin.name] = f"dropped tools {dropped} (built-in conflict)"
        filtered = Toolset(
            name=instance.name,
            description=instance.description,
            tools=plugin_tools,
            rules=instance.rules,
            planning_note=getattr(instance, "planning_note", "") or "",
        )
        registry.register_toolset(filtered)
        for tool in plugin_tools:
            registry.record_plugin_manifest(tool.name, plugin.manifest)
        return True

    report.failed[plugin.name] = f"unknown plugin kind {plugin.kind!r}"
    return False


def load_into(registry, skill_registry) -> LoadReport:
    """Discover plugins and register the eligible ones into the live registries."""
    report = LoadReport()

    builtin_tool_names = set(registry.tool_names())
    builtin_skill_names = set(skill_registry.names())

    plugins = discover_plugins()
    if not plugins:
        return report

    for plugin in plugins:
        report.by_plugin[plugin.name] = plugin.manifest

        missing = probe_dependencies(plugin.manifest.requires_python)
        if missing:
            reason = ", ".join(f"{m.requirement} ({m.reason})" for m in missing)
            report.disabled[plugin.name] = f"missing deps: {reason}"
            _emit_plugin_event(
                "plugin.dep_missing",
                plugin,
                extra={"missing": [m.requirement for m in missing]},
            )
            _emit_plugin_event("plugin.disabled", plugin, extra={"reason": reason})
            logger.warning(f"plugin: {plugin.name} disabled — missing deps: {reason}")
            continue

        obj, err = plugin.resolve_safely()
        if obj is None:
            report.failed[plugin.name] = err or "resolve failed"
            _emit_plugin_event("plugin.disabled", plugin, extra={"reason": err or "resolve failed"})
            logger.warning(f"plugin: {plugin.name} failed to resolve — {err}")
            continue

        ok = _register_one(
            plugin, obj, registry, skill_registry,
            builtin_tool_names, builtin_skill_names, report,
        )
        if ok:
            report.enabled.append(plugin.name)
            _emit_plugin_event("plugin.loaded", plugin)
            logger.info(f"plugin: {plugin.name} {plugin.manifest.version} enabled ({plugin.kind})")
        else:
            reason = report.conflicts.get(plugin.name) or report.failed.get(plugin.name) or "register failed"
            _emit_plugin_event("plugin.disabled", plugin, extra={"reason": reason})
            logger.warning(f"plugin: {plugin.name} not loaded — {reason}")

    if report.enabled or report.disabled or report.failed or report.conflicts:
        logger.info(report.summary_line())
    return report
