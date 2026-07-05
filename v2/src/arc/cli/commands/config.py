"""`arc config show` / `arc config path` — inspect resolved configuration."""
from __future__ import annotations

import sys


def _cmd_config_path(home_override: str | None) -> int:
    from arc.bootstrap import paths_for, resolve_home
    p = paths_for(resolve_home(home_override))
    print(p.config_file)
    return 0 if p.config_file.exists() else 1


def _cmd_config_show(home_override: str | None) -> int:
    """Print the resolved config (as YAML) for debugging."""
    from arc.bootstrap import paths_for, resolve_home
    p = paths_for(resolve_home(home_override))
    if not p.config_file.exists():
        print(f"no config at {p.config_file}", file=sys.stderr)
        print("run `arc bootstrap` to create one", file=sys.stderr)
        return 1
    # Just print the raw file contents — it's the source of truth
    print(p.config_file.read_text(), end="")
    return 0
