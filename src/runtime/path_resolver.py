"""Translate agent-facing virtual paths to real on-disk paths.

The agent operates on logical paths like `_analysis/<binary>/<file>` because that
prefix is baked into prompts, validators, and skill definitions. Internally those
files live under ARC_HOME (default ~/.arc/analysis/...) — this resolver maps
virtual paths to their real filesystem location at the boundary (write_file,
read_file, read_file_lines).

The mapping is a simple prefix substitution. Paths without a known virtual
prefix are returned unchanged so existing absolute and relative-to-CWD paths
keep working.
"""
from __future__ import annotations

from pathlib import Path

# Virtual agent-facing prefix → subdirectory under arc_home()
_VIRTUAL_PREFIXES: dict[str, str] = {
    "_analysis/": "analysis/",
}


def resolve_path(path: str) -> str:
    """Map a virtual path to its real filesystem path.

    `_analysis/proc/foo.c` → `<ARC_HOME>/analysis/proc/foo.c`
    `/abs/path` → unchanged
    `relative/path` → unchanged
    """
    if not path:
        return path
    from session_paths import arc_home
    for prefix, target in _VIRTUAL_PREFIXES.items():
        if path.startswith(prefix):
            remainder = path[len(prefix):]
            return str(arc_home() / target.rstrip("/") / remainder)
    return path


def to_virtual(real_path: str) -> str:
    """Map a real filesystem path back to the virtual agent-facing form.

    Used when reporting paths in tool output so the agent sees consistent
    `_analysis/...` paths even though files live under arc_home.
    """
    if not real_path:
        return real_path
    from session_paths import arc_home
    real = Path(real_path).resolve()
    home = arc_home().resolve()
    try:
        rel = real.relative_to(home)
    except ValueError:
        return real_path
    parts = rel.parts
    if not parts:
        return real_path
    for prefix, target in _VIRTUAL_PREFIXES.items():
        target_name = target.rstrip("/")
        if parts[0] == target_name:
            return prefix + "/".join(parts[1:])
    return real_path
