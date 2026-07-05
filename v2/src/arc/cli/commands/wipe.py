"""`arc wipe` — delete state under ARC_HOME.  See `arc/wipe.py`."""
from __future__ import annotations

import sys


def _cmd_wipe(home_override: str | None, args) -> int:
    from arc.bootstrap import resolve_home
    from arc.wipe import WipeTargets, build_plan, execute_plan, format_plan

    home = resolve_home(home_override)
    targets = WipeTargets(
        all_=args.wipe_all,
        sessions=args.sessions,
        llm=args.llm,
        history=args.history,
        pricing_cache=args.pricing_cache,
    ).with_default_if_empty()

    plan = build_plan(home, targets)
    if plan.is_noop:
        print(f"nothing to wipe under {home} (no matching files exist)")
        return 0

    print(format_plan(plan))

    if args.dry_run:
        print("(dry-run: no changes made)")
        return 0

    if not args.assume_yes:
        # No TTY → refuse silently rather than accidentally wiping in CI
        if not sys.stdin.isatty():
            print("aborted: not a TTY; pass --yes to confirm in non-interactive runs",
                  file=sys.stderr)
            return 1
        try:
            answer = input("proceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("aborted")
            return 1
        if answer not in ("y", "yes"):
            print("aborted")
            return 1

    removed = execute_plan(plan)
    print(f"wiped {len(removed)} path(s).")
    return 0
