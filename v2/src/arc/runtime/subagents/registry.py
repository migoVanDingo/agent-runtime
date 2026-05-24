"""Sub-agent discovery + override merging.

Three sources, last-wins precedence:
  1. Built-ins        (arc.runtime.subagents.builtins)
  2. Plugin entries   (arc.subagents entry-point group)
  3. Config overrides (subagents: block in config.yml)

Config can both DEFINE new specs (all required fields present) and
OVERRIDE field-level on existing plugin/builtin specs (any subset of
fields). The registry returns the final merged set.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from importlib.metadata import distributions, entry_points
from pathlib import Path
from typing import Any, Callable

from arc.runtime.subagents.builtins import all_builtins
from arc.runtime.subagents.spec import SubAgentSpec


# DO NOT rename without a major __api_version__ bump on arc.subagent_api.
ENTRY_POINT_GROUP = "arc.subagents"


@dataclass(frozen=True)
class SubAgentBuildContext:
    """Minimal v0.1 build context handed to plugin `build()` callables.

    Reserved for future expansion. `config` and `build_ctx` mirror the
    plugin builder signature so the two APIs feel parallel.
    """
    arc_home: Path


@dataclass(frozen=True)
class _DiscoveredSpec:
    """One entry-point-discovered spec before override merging."""
    spec: SubAgentSpec
    package: str
    package_version: str
    entry_point_value: str


@dataclass(frozen=True)
class _LoadFailure:
    """An entry point that couldn't be loaded."""
    name: str
    package: str
    entry_point_value: str
    error: str


@dataclass(frozen=True)
class _NameConflict:
    """An entry point whose name collides with a built-in or earlier entry."""
    name: str
    discovered_from: str
    conflicts_with: str           # "builtin:<name>" or "package:<name>"


@dataclass
class DiscoveryReport:
    """Observability around discovery — surfaced via CLI + events."""
    builtins: list[SubAgentSpec] = field(default_factory=list)
    plugins: list[_DiscoveredSpec] = field(default_factory=list)
    failures: list[_LoadFailure] = field(default_factory=list)
    conflicts: list[_NameConflict] = field(default_factory=list)
    config_only: list[str] = field(default_factory=list)  # names defined entirely in config
    config_overrides: list[str] = field(default_factory=list)  # names overridden by config


class SubAgentRegistry:
    """Holds the merged sub-agent registry for a session.

    Discovery is split from access so tests can inject fake builders without
    touching the entry-point machinery.
    """

    def __init__(
        self,
        *,
        builtins: dict[str, SubAgentSpec] | None = None,
        entry_point_loader: Callable[[], list[Any]] | None = None,
        arc_home: Path | None = None,
    ) -> None:
        self._builtins = dict(builtins) if builtins is not None else all_builtins()
        self._loader = entry_point_loader or _default_entry_point_loader
        self._arc_home = arc_home or Path.home() / ".arc"
        self._specs: dict[str, SubAgentSpec] = {}
        self._enabled: dict[str, bool] = {}
        self._report = DiscoveryReport()

    # ── Discovery ──────────────────────────────────────────────────────────

    def discover(self, subagents_config: dict | None = None) -> DiscoveryReport:
        """Build the merged spec registry. Idempotent — safe to call twice.

        `subagents_config` is the parsed `subagents:` block from config.yml,
        keyed by spec name. Each value is a dict of override fields plus an
        optional `enabled: bool`.
        """
        report = DiscoveryReport()
        merged: dict[str, SubAgentSpec] = {}
        enabled: dict[str, bool] = {}
        # Builder callables remembered per plugin name so step 3 can re-call
        # build() with user-provided config (enabling specs that need to render
        # prompts or set params from user keys, per 0022 §Config injection).
        plugin_builders: dict[str, tuple[Any, str]] = {}

        # 1. Built-ins
        for name, spec in self._builtins.items():
            merged[name] = spec
            enabled[name] = True
            report.builtins.append(spec)

        # 2. Plugin entry points
        for ep in self._loader():
            try:
                builder = ep.load()
            except Exception as exc:
                report.failures.append(_LoadFailure(
                    name=ep.name,
                    package=_owning_package(ep),
                    entry_point_value=ep.value,
                    error=f"{type(exc).__name__}: {exc}",
                ))
                continue
            if not callable(builder):
                report.failures.append(_LoadFailure(
                    name=ep.name,
                    package=_owning_package(ep),
                    entry_point_value=ep.value,
                    error=f"entry point resolved to non-callable {type(builder).__name__}",
                ))
                continue
            if ep.name in self._builtins:
                report.conflicts.append(_NameConflict(
                    name=ep.name,
                    discovered_from=_owning_package(ep),
                    conflicts_with=f"builtin:{ep.name}",
                ))
                continue
            if ep.name in merged and merged[ep.name].source == "plugin":
                report.conflicts.append(_NameConflict(
                    name=ep.name,
                    discovered_from=_owning_package(ep),
                    conflicts_with=f"package:{merged[ep.name].source_package}",
                ))
                continue
            ctx = SubAgentBuildContext(arc_home=self._arc_home)
            try:
                spec = builder({}, ctx)
            except Exception as exc:
                report.failures.append(_LoadFailure(
                    name=ep.name,
                    package=_owning_package(ep),
                    entry_point_value=ep.value,
                    error=f"build() raised {type(exc).__name__}: {exc}",
                ))
                continue
            if not isinstance(spec, SubAgentSpec):
                report.failures.append(_LoadFailure(
                    name=ep.name,
                    package=_owning_package(ep),
                    entry_point_value=ep.value,
                    error=f"build() returned {type(spec).__name__}, expected SubAgentSpec",
                ))
                continue
            pkg, ver = _package_meta(ep)
            tagged = replace(spec, source="plugin", source_package=pkg)
            merged[ep.name] = tagged
            enabled[ep.name] = True
            # Remember the builder so step 3 can re-call with user config.
            plugin_builders[ep.name] = (builder, pkg)
            report.plugins.append(_DiscoveredSpec(
                spec=tagged,
                package=pkg,
                package_version=ver,
                entry_point_value=ep.value,
            ))

        # 3. Config overrides + config-only definitions
        spec_field_names = {
            f.name for f in SubAgentSpec.__dataclass_fields__.values()
        }
        for name, raw in (subagents_config or {}).items():
            if not isinstance(raw, dict):
                raise ValueError(
                    f"sub-agent config entry {name!r} must be a mapping, got {type(raw).__name__}"
                )
            override_fields = {k: v for k, v in raw.items() if k != "enabled"}
            user_enabled = bool(raw.get("enabled", True))

            if name in merged:
                # If this is a plugin spec and the user supplied any config
                # keys, re-call `build()` with the user's full config so the
                # spec author can consume their own custom keys (rendering
                # prompts, setting params, etc.) Per 0022 §Config injection.
                if name in plugin_builders and override_fields:
                    builder, pkg = plugin_builders[name]
                    ctx = SubAgentBuildContext(arc_home=self._arc_home)
                    try:
                        rebuilt = builder(override_fields, ctx)
                        if isinstance(rebuilt, SubAgentSpec):
                            merged[name] = replace(
                                rebuilt, source="config", source_package=pkg,
                            )
                    except Exception as exc:
                        report.failures.append(_LoadFailure(
                            name=name, package=pkg,
                            entry_point_value=f"{name}.build (rebuild)",
                            error=f"rebuild with user config failed: "
                                  f"{type(exc).__name__}: {exc}",
                        ))

                # Then apply any keys that ARE valid SubAgentSpec fields as
                # field-level overrides on top of (possibly-rebuilt) spec.
                # Filter out keys the build() consumed — those aren't fields.
                field_overrides = {
                    k: v for k, v in override_fields.items()
                    if k in spec_field_names
                }
                if field_overrides:
                    merged[name] = merged[name].merged_with(field_overrides)

                if override_fields:
                    merged[name] = replace(merged[name], source="config")
                    report.config_overrides.append(name)
                enabled[name] = user_enabled
            else:
                # New config-only spec — must have all required fields.
                required = ("description", "provider", "model", "system_prompt")
                missing = [k for k in required if k not in override_fields]
                if missing:
                    raise ValueError(
                        f"sub-agent {name!r} defined in config is missing required "
                        f"fields: {missing}\n"
                        f"  required for new specs: {list(required)}"
                    )
                tools = override_fields.pop("tools", ())
                if not isinstance(tools, (list, tuple)):
                    raise ValueError(
                        f"sub-agent {name!r}: tools must be a list, got {type(tools).__name__}"
                    )
                spec = SubAgentSpec(
                    name=name,
                    description=str(override_fields.pop("description")),
                    provider=str(override_fields.pop("provider")),
                    model=str(override_fields.pop("model")),
                    system_prompt=str(override_fields.pop("system_prompt")),
                    tools=tuple(tools),
                    source="config",
                )
                if override_fields:
                    spec = spec.merged_with(override_fields)
                merged[name] = spec
                enabled[name] = user_enabled
                report.config_only.append(name)

        self._specs = merged
        self._enabled = enabled
        self._report = report
        return report

    # ── Access ─────────────────────────────────────────────────────────────

    def all_specs(self) -> dict[str, SubAgentSpec]:
        return dict(self._specs)

    def enabled_specs(self) -> dict[str, SubAgentSpec]:
        return {n: s for n, s in self._specs.items() if self._enabled.get(n, True)}

    def get(self, name: str) -> SubAgentSpec:
        if name not in self._specs:
            raise KeyError(f"sub-agent {name!r} not registered")
        return self._specs[name]

    def is_enabled(self, name: str) -> bool:
        return self._enabled.get(name, False)

    def report(self) -> DiscoveryReport:
        return self._report


# ── Entry-point machinery ──────────────────────────────────────────────────


def _default_entry_point_loader():
    """Iterate `arc.subagents` entry points across Python versions."""
    eps = entry_points()
    if hasattr(eps, "select"):
        return list(eps.select(group=ENTRY_POINT_GROUP))
    return list(eps.get(ENTRY_POINT_GROUP, []))  # type: ignore[attr-defined]


def _owning_package(ep) -> str:
    pkg, _ = _package_meta(ep)
    return pkg


def _package_meta(ep) -> tuple[str, str]:
    """Best-effort: locate the installed dist that owns an entry point."""
    try:
        for dist in distributions():
            try:
                for cand in dist.entry_points:
                    if (
                        cand.group == ENTRY_POINT_GROUP
                        and cand.name == ep.name
                        and cand.value == ep.value
                    ):
                        return (dist.metadata["Name"], dist.version)
            except Exception:
                continue
    except Exception:
        pass
    return ("unknown", "0")
