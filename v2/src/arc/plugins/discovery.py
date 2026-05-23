"""Out-of-tree plugin discovery via `arc.plugins` entry-point group.

External plugins ship as pip-installable packages that declare an entry point:

    [project.entry-points."arc.plugins"]
    <plugin_name> = "<dotted.path>:build"

At arc startup we walk `importlib.metadata.entry_points(group="arc.plugins")`,
resolve each entry point to a callable, and present it to the plugin loader
as if it were one of the built-in `_BUILDERS`.

Two layers of failure are isolated here:

  1. **Load-time** — if importing the entry-point module raises (the user's
     package is broken, has a syntax error, missing dependency), we capture
     it as a `LoadFailure` and continue. Other plugins still load.
  2. **Collision** — if a discovered name shadows a built-in name (e.g., an
     external `guard` plugin tries to override arc's `guard`), built-ins win
     and the discovered one is reported in `conflicts`. Surprising overrides
     are the worst kind of bug; loud-and-ignored is the right policy.

The result (a `DiscoveryReport`) is observable via events emitted by the
runtime so the user can see in `arc plugins` exactly what was found, what
was loaded, and what was skipped or shadowed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, distributions, entry_points
from typing import Any, Callable

# The entry-point group name. External packages reference this string in
# their pyproject.toml. DO NOT rename without a major __api_version__ bump.
ENTRY_POINT_GROUP = "arc.plugins"


@dataclass(frozen=True)
class DiscoveredPlugin:
    """One successfully discovered out-of-tree plugin.

    The builder callable has signature `build(config: dict, build_ctx) -> object`
    matching the contract for built-in `_BUILDERS` entries.
    """
    name: str
    builder: Callable[..., Any]
    package: str             # e.g. "arc-plugin-briefbot"
    package_version: str     # e.g. "0.2.1"
    entry_point_value: str   # e.g. "arc_plugin_briefbot.plugin:build"


@dataclass(frozen=True)
class LoadFailure:
    """A discovered entry point that couldn't be imported."""
    name: str
    package: str
    entry_point_value: str
    error: str  # str(exception) — full traceback goes to log


@dataclass(frozen=True)
class NameConflict:
    """A discovered plugin whose name collides with another plugin.

    `kind` is one of:
      "builtin"  — collides with a built-in plugin; built-in wins.
      "duplicate" — two external packages export the same name; first
                    discovered wins, others reported here.
    """
    name: str
    discovered_from: str  # package name
    conflicts_with: str   # "builtin:guard" or "package:arc-plugin-other"
    kind: str


@dataclass
class DiscoveryReport:
    """What entry-point discovery found. Observable, replay-friendly."""
    discovered: list[DiscoveredPlugin] = field(default_factory=list)
    failures: list[LoadFailure] = field(default_factory=list)
    conflicts: list[NameConflict] = field(default_factory=list)

    def by_name(self) -> dict[str, DiscoveredPlugin]:
        return {d.name: d for d in self.discovered}


def discover(*, builtin_names: set[str]) -> DiscoveryReport:
    """Walk the entry-point group and return what was found.

    `builtin_names` is the set of plugin names that ship with arc — used to
    detect external plugins trying to shadow them. Built-ins always win.

    Idempotent and side-effect-free apart from importing user code (which is
    necessary to resolve the builder callable).
    """
    report = DiscoveryReport()
    seen_names: dict[str, str] = {}  # name → package that registered it

    for ep in _iter_entry_points():
        package, version = _owning_distribution(ep)
        if ep.name in builtin_names:
            report.conflicts.append(NameConflict(
                name=ep.name,
                discovered_from=package,
                conflicts_with=f"builtin:{ep.name}",
                kind="builtin",
            ))
            continue
        if ep.name in seen_names:
            report.conflicts.append(NameConflict(
                name=ep.name,
                discovered_from=package,
                conflicts_with=f"package:{seen_names[ep.name]}",
                kind="duplicate",
            ))
            continue
        try:
            builder = ep.load()
        except Exception as exc:  # noqa: BLE001 — load isolation is the point
            report.failures.append(LoadFailure(
                name=ep.name,
                package=package,
                entry_point_value=ep.value,
                error=f"{type(exc).__name__}: {exc}",
            ))
            continue

        if not callable(builder):
            report.failures.append(LoadFailure(
                name=ep.name,
                package=package,
                entry_point_value=ep.value,
                error=f"entry point resolved to non-callable {type(builder).__name__}",
            ))
            continue

        seen_names[ep.name] = package
        report.discovered.append(DiscoveredPlugin(
            name=ep.name,
            builder=builder,
            package=package,
            package_version=version,
            entry_point_value=ep.value,
        ))

    return report


# ── Helpers ───────────────────────────────────────────────────────────────


def _iter_entry_points():
    """`entry_points(group=...)` returns different shapes across Python versions.
    Normalize to an iterable of EntryPoint objects.
    """
    eps = entry_points()
    if hasattr(eps, "select"):
        # Python 3.10+ EntryPoints object
        return list(eps.select(group=ENTRY_POINT_GROUP))
    # Older dict-style fallback (3.9 and earlier) — kept defensive
    return list(eps.get(ENTRY_POINT_GROUP, []))  # type: ignore[attr-defined]


def _owning_distribution(ep: EntryPoint) -> tuple[str, str]:
    """Best-effort: figure out which installed package an entry point came from.

    Walks `distributions()` looking for a matching entry point. Returns
    (package_name, version) or ("unknown", "0") if we can't locate it (which
    would be very unusual but we don't want to crash discovery over it).
    """
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
            except Exception:  # noqa: BLE001 — defensive
                continue
    except Exception:  # noqa: BLE001 — defensive
        pass
    return ("unknown", "0")
