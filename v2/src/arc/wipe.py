"""`arc wipe` — selectively delete state under ARC_HOME.

Useful during development when you want to test a clean bootstrap, or to
clear out accumulated session logs without re-picking your provider.

Defaults to "wipe sessions only" because that's the everyday dev cycle.
`--all` removes the whole ARC_HOME tree (next `arc bootstrap` writes it
fresh).

Safety:
  - Never touches anything outside the resolved ARC_HOME.
  - Prompts before deleting unless `--yes` is passed.
  - `--dry-run` prints what would be removed and exits.
  - Refuses to wipe `llm/` (with the running llama-server PID file) unless
    the user confirms — accidentally orphaning a server process is annoying.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path


# ── Public dataclasses ────────────────────────────────────────────────────


@dataclass(frozen=True)
class WipeTargets:
    """Which subtrees to wipe.  Mutually inclusive (combine freely).

    `all_` is the un-bootstrap: remove ARC_HOME entirely.  When True the
    other flags are ignored — the whole tree goes.
    """
    all_: bool = False
    sessions: bool = False
    llm: bool = False
    history: bool = False
    pricing_cache: bool = False

    @property
    def is_empty(self) -> bool:
        return not any([
            self.all_, self.sessions, self.llm, self.history, self.pricing_cache,
        ])

    def with_default_if_empty(self) -> WipeTargets:
        """If the user gave no flags, default to sessions only."""
        if self.is_empty:
            return WipeTargets(sessions=True)
        return self


@dataclass
class WipePlan:
    """What `wipe` is about to do.  Built from a WipeTargets + the resolved
    HomePaths."""
    home: Path
    paths_to_remove: list[Path] = field(default_factory=list)
    pid_file_present: bool = False  # only set when llm/ is targeted

    @property
    def is_noop(self) -> bool:
        return not self.paths_to_remove

    def total_size_bytes(self) -> int:
        total = 0
        for p in self.paths_to_remove:
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
            elif p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file():
                        try:
                            total += f.stat().st_size
                        except OSError:
                            pass
        return total


# ── Plan + execute ────────────────────────────────────────────────────────


def build_plan(home: Path, targets: WipeTargets) -> WipePlan:
    """Inspect the filesystem and produce a plan of files/dirs to remove.

    Only paths that actually exist are added — wipe is idempotent.
    """
    from arc.bootstrap import paths_for
    p = paths_for(home)
    plan = WipePlan(home=home)

    if targets.all_:
        # Full nuke: the whole ARC_HOME directory (if it exists)
        if home.exists():
            plan.paths_to_remove.append(home)
            # Surface whether a server PID file exists so the caller can warn
            if p.llm_dir.is_dir() and (p.llm_dir / "current.pid").exists():
                plan.pid_file_present = True
        return plan

    if targets.sessions:
        if p.sessions_dir.is_dir():
            plan.paths_to_remove.append(p.sessions_dir)

    if targets.llm:
        if p.llm_dir.is_dir():
            plan.paths_to_remove.append(p.llm_dir)
            if (p.llm_dir / "current.pid").exists():
                plan.pid_file_present = True

    if targets.history:
        hist = home / "history"
        if hist.is_file():
            plan.paths_to_remove.append(hist)

    if targets.pricing_cache:
        cache = home / "pricing_cache.json"
        if cache.is_file():
            plan.paths_to_remove.append(cache)

    return plan


def execute_plan(plan: WipePlan) -> list[Path]:
    """Remove everything in `plan.paths_to_remove`.  Returns the paths
    that were actually removed (in case any were lost to a race)."""
    removed: list[Path] = []
    for p in plan.paths_to_remove:
        if not p.exists():
            continue
        # Sanity: never remove anything outside `home`.  shutil.rmtree on a
        # path that resolves outside home would be catastrophic.
        try:
            p.relative_to(plan.home)
        except ValueError:
            # `p` IS plan.home (when --all).  That's fine; the inverse
            # relative_to of `home` from itself raises ValueError on some
            # Python versions, but the path IS home so accept it.
            if p.resolve() != plan.home.resolve():
                continue

        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        except FileNotFoundError:
            continue
        removed.append(p)
    return removed


# ── Rendering ─────────────────────────────────────────────────────────────


def format_plan(plan: WipePlan) -> str:
    """Human-readable summary of the plan for the confirmation prompt."""
    if plan.is_noop:
        return f"nothing to wipe under {plan.home}"
    lines = [f"about to remove (under {plan.home}):"]
    for p in plan.paths_to_remove:
        try:
            rel = p.relative_to(plan.home)
            display = "." if str(rel) == "." else str(rel) + ("/" if p.is_dir() else "")
        except ValueError:
            display = str(p)
        lines.append(f"  - {display}")
    size = plan.total_size_bytes()
    lines.append(f"  total: ~{_format_bytes(size)}")
    if plan.pid_file_present:
        lines.append(
            "  NOTE: an `arc llm` PID file is present.  Removing it WILL NOT "
            "stop the llama-server process — run `arc llm stop` first if you "
            "want the server killed too."
        )
    return "\n".join(lines)


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"
