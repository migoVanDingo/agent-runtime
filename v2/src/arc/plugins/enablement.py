"""First-run enablement for newly-discovered out-of-tree plugins.

Lifecycle:
  1. arc starts. Entry-point discovery (in `discovery.py`) runs at import
     time and populates `_BUILDERS`.
  2. arc loads `config.yml`. Some plugins are listed; some aren't.
  3. THIS module compares the two and, for any *new* plugin (discovered but
     not in config), prompts the user (interactive only) and persists the
     answer to `config.yml`.
  4. arc reloads config (the file may have changed) and proceeds normally.

The flow is observable: a `RuntimeEvent` is emitted for each prompt,
acceptance, and decline so replay and `arc plugins` reflect what happened.

Interactive vs headless:
  - Interactive (`arc`): prompt via stdin (TTY). Defaults to "yes" — the
    user explicitly installed the plugin, the friction tax is one keystroke.
  - Headless (`arc run`, batch, CI): NEVER prompt. Discovered-but-not-in-
    config plugins stay dormant. This matches the existing NoOpGate policy
    that headless never escalates.

Reversibility:
  - "Yes" writes `enabled: true` to config.yml.
  - "No" writes `enabled: false` to config.yml — so the user is NOT
    re-prompted on next boot. They can flip via `arc plugins`.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from arc.config import PluginsConfig
from arc.plugins.discovery import DiscoveredPlugin, DiscoveryReport
from arc.setup.writer import write_plugin_enablement


@dataclass(frozen=True)
class EnablementOutcome:
    """One plugin's first-run decision, suitable for event emission."""
    name: str
    package: str
    package_version: str
    enabled: bool
    persisted: bool   # whether config.yml was updated
    skipped_reason: str | None = None  # set when prompt was suppressed


def find_new_plugins(
    report: DiscoveryReport,
    cfg: PluginsConfig,
) -> list[DiscoveredPlugin]:
    """Return plugins that were discovered but have no entry (enabled or
    disabled) in config.yml. These are the ones that need a first-run
    decision.
    """
    in_config = {entry.name for entry in cfg.enabled}
    return [d for d in report.discovered if d.name not in in_config]


def run_first_run_flow(
    config_path: Path,
    *,
    new_plugins: list[DiscoveredPlugin],
    interactive: bool,
    prompt_fn: Callable[[str], bool] | None = None,
    emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> list[EnablementOutcome]:
    """For each new plugin, prompt (or skip) and persist the answer.

    `interactive`: True for `arc` (TTY), False for `arc run` and other
                   non-interactive entry points.
    `prompt_fn`:   override the y/n prompt for tests (default: stdin).
    `emit`:        callback to emit events. Signature (event_type, payload).
                   Optional — when None, the flow runs silently except for
                   persistence side effects.

    Returns one EnablementOutcome per new plugin (even if skipped).
    """
    if not new_plugins:
        return []

    prompt = prompt_fn or _default_prompt
    emit = emit or (lambda _t, _p: None)
    outcomes: list[EnablementOutcome] = []

    for d in new_plugins:
        emit("plugin.first_run.prompted", {
            "name": d.name,
            "package": d.package,
            "package_version": d.package_version,
            "interactive": interactive,
        })

        if not interactive:
            outcomes.append(EnablementOutcome(
                name=d.name,
                package=d.package,
                package_version=d.package_version,
                enabled=False,
                persisted=False,
                skipped_reason="headless mode — no prompt",
            ))
            continue

        question = (
            f"[+] new arc plugin discovered: {d.name} "
            f"(from {d.package} v{d.package_version})\n"
            f"    enable it for this and future sessions?"
        )
        approved = prompt(question)

        write_plugin_enablement(
            config_path, name=d.name, enabled=approved,
        )
        emit(
            "plugin.first_run.enabled" if approved else "plugin.first_run.declined",
            {"name": d.name, "package": d.package},
        )
        emit("plugin.config.persisted", {
            "name": d.name, "enabled": approved, "config_path": str(config_path),
        })
        outcomes.append(EnablementOutcome(
            name=d.name,
            package=d.package,
            package_version=d.package_version,
            enabled=approved,
            persisted=True,
        ))

    return outcomes


# ── Default prompt ────────────────────────────────────────────────────────


def _default_prompt(question: str) -> bool:
    """Read y/n from stdin. Defaults to "yes" (user installed it on purpose).

    Returns False on EOF or KeyboardInterrupt — never raise back to caller.
    """
    if not sys.stdin.isatty():
        return False
    sys.stdout.write(question + " [Y/n] ")
    sys.stdout.flush()
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write("\n(declined)\n")
        return False
    return answer in ("", "y", "yes")
