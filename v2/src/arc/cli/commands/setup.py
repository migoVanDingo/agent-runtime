"""`arc setup` — interactive setup hub (provider, plugins, themes, …)."""
from __future__ import annotations

import sys

import arc.cli as _cli


def _cmd_setup(
    home_override: str | None,
    *,
    provider: str | None,
    model: str | None,
    print_only: bool,
    no_launch: bool,
    hub: bool = True,
    section: str | None = None,
) -> int:
    """`arc setup` — opens the interactive setup hub by default.

    Behavior matrix:
      arc setup                 → hub (sidebar + content; navigates to every section)
      arc setup --picker        → classic provider/model picker (0017), then launch TUI
      arc setup --provider X    → non-interactive write (preserves prior contract)
      arc setup --section NAME  → hub focused on NAME

    See _design/0023-setup-hub-and-themes.md for the hub, 0017 for the picker.
    """
    from arc.bootstrap import bootstrap, resolve_home
    from arc.setup import run_setup
    from arc.setup.hub import run_hub

    if model is not None and provider is None:
        print("--model requires --provider", file=sys.stderr)
        return 2

    # No flags + hub enabled → open the hub (the default path).
    if hub and provider is None and model is None and not print_only:
        home = resolve_home(home_override)
        # Hub assumes ARC_HOME exists; bootstrap if missing (idempotent).
        bootstrap(home)
        result = run_hub(home, initial_section=section)
        if result.launch_session:
            return _cli._cmd_interactive(home_override)
        return result.rc

    try:
        result = run_setup(
            home=resolve_home(home_override),
            provider_override=provider,
            model_override=model,
            print_only=print_only,
        )
    except SystemExit as exc:
        # run_setup raises SystemExit on abort/error with a clear message
        print(str(exc.code) if exc.code and not isinstance(exc.code, int) else "aborted",
              file=sys.stderr)
        return 1 if exc.code else 0

    if print_only:
        return 0

    print(f"arc setup → {result.provider}/{result.model}")
    print(f"  config: {result.config_path}")
    print(result.diff_text)
    if result.api_key_warning:
        print(f"  warning: {result.api_key_warning}", file=sys.stderr)

    # Auto-launch the TUI if the user just walked the interactive picker.
    # Skip for scripted mode (flags-only), --no-launch, or missing api key
    # — the last one would just fail at provider construction.
    interactive_path = provider is None and model is None
    if not interactive_path:
        return 0
    if no_launch:
        return 0
    if result.api_key_warning:
        print("  (skipping launch — fix the env var above, then run `arc`)",
              file=sys.stderr)
        return 0

    print()
    print(f"starting session against {result.provider}/{result.model}…")
    return _cli._cmd_interactive(home_override)
