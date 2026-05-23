"""Default pattern catalog for the destructive-action gate.

Each entry has:
  name        — short, stable id used as the key in the remember-cache
                and in safety.confirmation.* event payloads
  description — shown to the user in the approval prompt
  regex       — applied to call.input["command"]
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pattern:
    name: str
    description: str
    regex: str


DEFAULT_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        name="rm-file",
        description="single-file rm (irreversible delete)",
        # Word-boundary `rm` followed by any non-flag arg. Matches `rm foo`,
        # `rm ./foo`. Does NOT match `rm -r foo` (caught by rm-recursive)
        # or `rm -rf` (caught by guard's blocklist).
        regex=r"\brm\s+(?!-)",
    ),
    Pattern(
        name="rm-recursive",
        description="recursive rm (rm -r without -f, which guard already bans)",
        regex=r"\brm\s+-r(?!f)",
    ),
    Pattern(
        name="git-reset-hard",
        description="git reset --hard (discards uncommitted changes)",
        regex=r"\bgit\s+reset\s+--hard\b",
    ),
    Pattern(
        name="git-clean-force",
        description="git clean -f / -fd (removes untracked files)",
        regex=r"\bgit\s+clean\s+-\w*f\w*\b",
    ),
    Pattern(
        name="git-push-force",
        description="git push --force / -f (overwrites remote history)",
        regex=r"\bgit\s+push\s+(?:--force(?:-with-lease)?|-f)\b",
    ),
    Pattern(
        name="truncate",
        description="truncate -s (resizes/empties file in place)",
        regex=r"\btruncate\s+-s\b",
    ),
    Pattern(
        name="chown-recursive",
        description="chown -R (recursive ownership change)",
        regex=r"\bchown\s+-R\b",
    ),
    Pattern(
        name="chmod-recursive",
        description="chmod -R (recursive permission change)",
        regex=r"\bchmod\s+-R\b",
    ),
    Pattern(
        name="redirect-overwrite",
        description="> file (single redirect overwrites existing file)",
        # Single `>` not preceded or followed by another `>`. Avoids matching `>>`.
        regex=r"(?<![>])>(?![>])\s*[^\s>&]",
    ),
    Pattern(
        name="drop-table",
        description="SQL DROP TABLE",
        regex=r"(?i)\bdrop\s+table\b",
    ),
    Pattern(
        name="drop-database",
        description="SQL DROP DATABASE",
        regex=r"(?i)\bdrop\s+database\b",
    ),
    Pattern(
        name="truncate-sql",
        description="SQL TRUNCATE",
        regex=r"(?i)\btruncate\s+(?:table\s+)?\w+",
    ),
)


def catalog_by_name() -> dict[str, Pattern]:
    """{pattern.name: Pattern} for lookups."""
    return {p.name: p for p in DEFAULT_PATTERNS}
