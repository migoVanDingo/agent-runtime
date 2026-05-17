"""Process-level sub-agent spec registry.

Specs register here at module import time (built-ins) or via the plugin
loader (future — 0091). Lookup by name. Single-spec-per-name; re-register
warns and replaces.

Why module-level: sub-agent dispatch needs to resolve names from anywhere
(tools, skills, the upcoming `arc subagent` CLI) without threading a
registry handle through every call site.
"""
from __future__ import annotations

from typing import Iterator

from logger import get_logger
from runtime.subagents.spec import SubAgentSpec

logger = get_logger(__name__)


_REGISTRY: dict[str, SubAgentSpec] = {}


def register_spec(spec: SubAgentSpec) -> None:
    """Add a spec. If a spec with the same name exists, log a warning and replace."""
    existing = _REGISTRY.get(spec.name)
    if existing is spec:
        return
    if existing is not None:
        logger.warning(
            f"subagent spec {spec.name!r} re-registered "
            f"(was: {existing.description!r}, now: {spec.description!r})"
        )
    _REGISTRY[spec.name] = spec


def get_spec(name: str) -> SubAgentSpec | None:
    return _REGISTRY.get(name)


def known_specs() -> list[str]:
    return sorted(_REGISTRY)


def all_specs() -> Iterator[SubAgentSpec]:
    yield from _REGISTRY.values()


def clear_for_tests() -> None:
    """Drop all registered specs. ONLY for use by unit tests."""
    _REGISTRY.clear()
