"""`arc bootstrap` — create ARC_HOME + default config."""
from __future__ import annotations


def _cmd_bootstrap(home_override: str | None, *, force: bool) -> int:
    from arc.bootstrap import bootstrap, format_bootstrap_summary, resolve_home
    home = resolve_home(home_override)
    result = bootstrap(home, force_config=force)
    print(format_bootstrap_summary(result))
    return 0
