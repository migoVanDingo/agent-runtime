"""`arc plugins` — manage installed plugins."""
from __future__ import annotations

import arc.cli as _cli


def _cmd_plugins(home_override: str | None, *, action: str | None) -> int:
    """`arc plugins` — manage installed plugins.

    No action  → opens the setup hub on the Plugins section.
    list       → non-interactive plain-text status table.
    """
    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.setup.hub import run_hub
    from arc.setup.plugin_menu import list_plugins

    home = resolve_home(home_override)
    bootstrap(home)
    paths = paths_for(home)

    if action == "list":
        return list_plugins(paths.config_file)
    result = run_hub(home, initial_section="plugins")
    if result.launch_session:
        return _cli._cmd_interactive(home_override)
    return result.rc
