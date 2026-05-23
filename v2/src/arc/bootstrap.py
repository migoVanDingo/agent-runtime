"""Home directory resolution + `arc bootstrap` logic.

The home dir layout is defined in _design/0001-foundation-phase0-design.md §7.
Resolution order is from §7.4: env vars first, then optional --home override.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from arc.defaults import (
    DEFAULT_CATALOG_YAML,
    DEFAULT_CONFIG_YAML,
    DEFAULT_LLM_SERVERS_YAML,
)

# ── Constants (no magic strings elsewhere in the codebase) ──────────────────

ARC_HOME = "ARC_HOME"
DEFAULT_HOME = "~/.arc"
CONFIG_FILENAME = "config.yml"
CATALOG_FILENAME = "catalog.yml"
LLM_SERVERS_FILENAME = "llm_servers.yml"
SESSIONS_DIRNAME = "sessions"
SESSIONS_INDEX_FILENAME = "index.jsonl"
LLM_DIRNAME = "llm"


@dataclass(frozen=True)
class HomePaths:
    """All paths derived from the resolved home directory.

    Building these once at startup means every other module asks the runtime
    for the path it needs, never recomputes from env vars.
    """

    home: Path
    config_file: Path
    catalog_file: Path
    llm_servers_file: Path
    llm_dir: Path
    sessions_dir: Path
    sessions_index: Path


def resolve_home(cli_override: str | None = None) -> Path:
    """Resolve the arc home directory.

    Resolution order, highest precedence first:
      1. cli_override — the `--home` flag
      2. $ARC_HOME    — full path (e.g., "~/.arc", "~/projects/p1/.arc",
                        "/abs/path/foo"). Tilde + env vars are expanded.
      3. default      — "~/.arc" (expands to $HOME/.arc)

    The whole path is one value — no separate "parent dir" and "folder name"
    knobs. If you want the folder named `.arc` inside your project, just set
    ARC_HOME to that full path.

    Returns the absolute path. Does NOT create directories — that's bootstrap's
    job.
    """
    # Use .get() not [] — env var may legitimately be unset, in which case
    # DEFAULT_HOME kicks in via the `or`. With [] this would KeyError.
    raw = cli_override or os.environ.get(ARC_HOME) or DEFAULT_HOME
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def paths_for(home: Path) -> HomePaths:
    """Derive all standard paths from a home dir. Pure function."""
    sessions = home / SESSIONS_DIRNAME
    return HomePaths(
        home=home,
        config_file=home / CONFIG_FILENAME,
        catalog_file=home / CATALOG_FILENAME,
        llm_servers_file=home / LLM_SERVERS_FILENAME,
        llm_dir=home / LLM_DIRNAME,
        sessions_dir=sessions,
        sessions_index=sessions / SESSIONS_INDEX_FILENAME,
    )


@dataclass
class BootstrapResult:
    """What bootstrap actually did. Used by the CLI to print a summary."""

    home: Path
    created_home: bool = False
    wrote_config: bool = False
    wrote_catalog: bool = False
    wrote_llm_servers: bool = False
    created_sessions_dir: bool = False
    created_sessions_index: bool = False
    created_llm_dir: bool = False

    @property
    def changed_anything(self) -> bool:
        return any(
            [
                self.created_home,
                self.wrote_config,
                self.wrote_catalog,
                self.wrote_llm_servers,
                self.created_sessions_dir,
                self.created_sessions_index,
                self.created_llm_dir,
            ]
        )


def bootstrap(home: Path, *, force_config: bool = False) -> BootstrapResult:
    """Create the home dir layout if missing. Idempotent.

    Args:
        home: resolved home directory (from resolve_home)
        force_config: if True, overwrite an existing config.yml,
                      catalog.yml, and llm_servers.yml

    Behavior:
        - Creates home/ if missing
        - Writes config.yml from DEFAULT_CONFIG_YAML if missing (or force)
        - Writes catalog.yml from DEFAULT_CATALOG_YAML if missing (or force) — 0017
        - Writes llm_servers.yml from DEFAULT_LLM_SERVERS_YAML if missing (or force) — 0018
        - Creates sessions/ if missing
        - Creates sessions/index.jsonl as empty file if missing
        - Creates llm/ if missing (for arc llm pid + log files, 0018)
        - Never touches existing sessions
        - Never touches existing user files unless force=True
    """
    p = paths_for(home)
    result = BootstrapResult(home=home)

    if not p.home.exists():
        p.home.mkdir(parents=True, exist_ok=True)
        result.created_home = True

    if not p.config_file.exists() or force_config:
        p.config_file.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
        result.wrote_config = True

    if not p.catalog_file.exists() or force_config:
        p.catalog_file.write_text(DEFAULT_CATALOG_YAML, encoding="utf-8")
        result.wrote_catalog = True

    if not p.llm_servers_file.exists() or force_config:
        p.llm_servers_file.write_text(DEFAULT_LLM_SERVERS_YAML, encoding="utf-8")
        result.wrote_llm_servers = True

    if not p.sessions_dir.exists():
        p.sessions_dir.mkdir(parents=True, exist_ok=True)
        result.created_sessions_dir = True

    if not p.sessions_index.exists():
        p.sessions_index.touch()
        result.created_sessions_index = True

    if not p.llm_dir.exists():
        p.llm_dir.mkdir(parents=True, exist_ok=True)
        result.created_llm_dir = True

    return result


def format_bootstrap_summary(result: BootstrapResult) -> str:
    """Human-readable one-block summary for the CLI to print."""
    if not result.changed_anything:
        return f"arc home: {result.home}  (no changes; already bootstrapped)"

    lines = [f"arc home: {result.home}"]
    if result.created_home:
        lines.append("  + created home directory")
    if result.wrote_config:
        lines.append(f"  + wrote {CONFIG_FILENAME}")
    if result.wrote_catalog:
        lines.append(f"  + wrote {CATALOG_FILENAME}")
    if result.wrote_llm_servers:
        lines.append(f"  + wrote {LLM_SERVERS_FILENAME}")
    if result.created_sessions_dir:
        lines.append(f"  + created {SESSIONS_DIRNAME}/")
    if result.created_sessions_index:
        lines.append(f"  + created {SESSIONS_DIRNAME}/{SESSIONS_INDEX_FILENAME}")
    if result.created_llm_dir:
        lines.append(f"  + created {LLM_DIRNAME}/")
    return "\n".join(lines)
