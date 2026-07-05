"""`arc compare` — side-by-side comparison of two or more recorded sessions (0019)."""
from __future__ import annotations

import sys


def t_short(target) -> str:  # tiny shim, batch.BatchTarget has .short()
    return target.short()


def _cmd_compare(
    home_override: str | None,
    *,
    session_ids: list[str],
    full: bool,
) -> int:
    """`arc compare` — side-by-side comparison of N sessions (0019)."""
    from arc.bootstrap import paths_for, resolve_home
    from arc.replay.compare import render_full_comparison
    from arc.tui.pricing import PricingTable

    if len(session_ids) < 2:
        print("arc compare: need at least 2 session ids", file=sys.stderr)
        return 2

    home = resolve_home(home_override)
    paths = paths_for(home)
    dirs = [paths.sessions_dir / sid for sid in session_ids]
    missing = [d for d in dirs if not (d / "events.jsonl").is_file()]
    if missing:
        for d in missing:
            print(f"compare: missing events.jsonl in {d}", file=sys.stderr)
        return 1

    if full:
        # Verbose mode: just dump the events files side-by-side
        for d in dirs:
            print(f"\n========== {d.name} ==========")
            print((d / "events.jsonl").read_text())
        return 0

    table = PricingTable(cache_path=home / "pricing_cache.json")
    print(render_full_comparison(dirs, pricing_table=table))
    return 0
