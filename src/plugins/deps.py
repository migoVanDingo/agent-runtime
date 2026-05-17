"""Plugin dependency probing.

Plugins declare their Python dependencies as PEP 508 requirement strings:

    [plugin.requires]
    python = ["camelot-py>=0.11", "pdfplumber>=0.10"]

The loader probes each requirement before registering the plugin. Missing or
version-incompatible dependencies disable the plugin (with a clear log line)
rather than crashing agent startup.

We use ``importlib.metadata`` for the version check — it's fast (no actual
import of the third-party module) and matches what ``pip`` would see.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import metadata


_REQ_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"\s*(?P<op>==|!=|>=|<=|>|<|~=)?"
    r"\s*(?P<version>[A-Za-z0-9._-]+)?\s*$"
)


@dataclass(frozen=True)
class MissingDep:
    requirement: str
    distribution: str
    reason: str  # "not installed" | "version mismatch: have X, need Y"


def _parse_requirement(req: str) -> tuple[str, str | None, str | None]:
    """Return (name, operator, version). Operator/version may be None."""
    m = _REQ_RE.match(req)
    if not m:
        return (req.strip(), None, None)
    return (m.group("name"), m.group("op"), m.group("version"))


def _parse_version_tuple(v: str) -> tuple[int, ...]:
    """Lossy version compare — splits on dots, ignores trailing non-numerics."""
    parts: list[int] = []
    for part in v.split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            parts.append(int(digits))
        else:
            break
    return tuple(parts) if parts else (0,)


def _version_satisfies(installed: str, op: str, required: str) -> bool:
    iv = _parse_version_tuple(installed)
    rv = _parse_version_tuple(required)
    if op == "==":
        return iv == rv
    if op == "!=":
        return iv != rv
    if op == ">=":
        return iv >= rv
    if op == "<=":
        return iv <= rv
    if op == ">":
        return iv > rv
    if op == "<":
        return iv < rv
    if op == "~=":
        # Compatible release: ~=1.4 ⇒ >=1.4, <2; ~=1.4.5 ⇒ >=1.4.5, <1.5
        if len(rv) < 1:
            return iv >= rv
        upper = rv[:-1] + (rv[-1] + 1,) if len(rv) > 1 else (rv[0] + 1,)
        return iv >= rv and iv < upper
    return True


def probe_dependencies(requirements: tuple[str, ...] | list[str]) -> list[MissingDep]:
    """Return the list of unsatisfied requirements (empty if all available)."""
    missing: list[MissingDep] = []
    for req in requirements:
        if not req:
            continue
        name, op, version = _parse_requirement(req)
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(MissingDep(requirement=req, distribution=name, reason="not installed"))
            continue
        if op and version:
            if not _version_satisfies(installed, op, version):
                missing.append(MissingDep(
                    requirement=req,
                    distribution=name,
                    reason=f"version mismatch: have {installed}, need {op}{version}",
                ))
    return missing
