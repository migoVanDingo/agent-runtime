"""`arc timeline` — regenerate and/or open the visual session timeline (0027)."""
from __future__ import annotations

import sys


def _cmd_timeline(home_override: str | None, *, open_browser: bool, rebuild: bool) -> int:
    from arc.bootstrap import paths_for, resolve_home
    from arc.config import load
    from arc.plugins.timeline.plugin import regenerate

    home = resolve_home(home_override)
    paths = paths_for(home)
    sessions_dir = paths.sessions_dir
    if not sessions_dir.is_dir():
        print(f"timeline: no sessions dir at {sessions_dir}", file=sys.stderr)
        return 1

    # Pull truncation knobs from the timeline plugin config if present.
    smax, omax = 400, 20000
    try:
        cfg = load(paths.config_file)
        for entry in cfg.plugins.enabled:
            if entry.name == "timeline":
                smax = int(entry.config.get("summary_max_chars", smax))
                omax = int(entry.config.get("full_output_max_chars", omax))
                break
    except Exception:
        pass  # config unreadable → defaults are fine

    timeline_path = sessions_dir / "timeline.html"
    stale = not timeline_path.exists()
    if rebuild or stale:
        timeline_path = regenerate(
            sessions_dir, rebuild_all=rebuild,
            summary_max_chars=smax, full_output_max_chars=omax,
        )
        print(f"timeline: {'rebuilt' if rebuild else 'generated'} {timeline_path}")
    else:
        print(f"timeline: {timeline_path}  (use --rebuild to regenerate)")

    if open_browser:
        import webbrowser
        webbrowser.open(timeline_path.as_uri())
    return 0
