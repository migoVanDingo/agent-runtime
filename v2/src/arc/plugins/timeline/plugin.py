"""timeline plugin (0027 phase d).

On session end, keeps the visual timeline fresh: caches this session's
node summary + writes its `session.html`, then rescans metas and rewrites the
global `timeline.html`. The rebuild is cheap — only the just-ended session
re-parses events; every other session is read from its cached
`timeline.node.json` (written here).

Plugin failure is quarantined by the runtime like any other; a broken
timeline never touches a session (principle 5).
"""
from __future__ import annotations

from pathlib import Path


class TimelinePlugin:
    name = "timeline"
    version = "1.0.0"

    def __init__(self, *, sessions_dir: Path, session_id: str,
                 summary_max_chars: int = 400, full_output_max_chars: int = 20000) -> None:
        self._sessions_dir = sessions_dir
        self._session_id = session_id
        self._summary_max_chars = summary_max_chars
        self._full_output_max_chars = full_output_max_chars

    def on_session_end(self, ctx, outcome) -> None:
        session_dir = self._sessions_dir / self._session_id
        if not session_dir.is_dir():
            return
        regenerate(
            self._sessions_dir,
            just_ended=self._session_id,
            summary_max_chars=self._summary_max_chars,
            full_output_max_chars=self._full_output_max_chars,
        )


def regenerate(sessions_dir: Path, *, just_ended: str | None = None,
               summary_max_chars: int = 400, full_output_max_chars: int = 20000,
               rebuild_all: bool = False) -> Path:
    """Write per-session artifacts + timeline.html; return the timeline path.

    `just_ended`: refresh only this session's node cache + session.html (the
    common on_session_end path). `rebuild_all`: refresh every session's (the
    `arc timeline --rebuild` recovery path). Sessions not refreshed keep their
    existing cache + session.html; only timeline.html always gets rewritten.
    """
    from arc.timeline.detail import build_session_detail
    from arc.timeline.render import render_session_html, render_timeline_html
    from arc.timeline.scan import scan_forest
    from arc.timeline.summarize import write_node_cache

    if rebuild_all:
        refresh = [c.name for c in sessions_dir.iterdir()
                   if c.is_dir() and c.name.startswith("SES")]
    elif just_ended is not None:
        refresh = [just_ended]
    else:
        refresh = []

    # 1. Refresh node caches so the scan below reads fresh summaries.
    for sid in refresh:
        sd = sessions_dir / sid
        if sd.is_dir():
            write_node_cache(sd, summary_max_chars=summary_max_chars)

    # 2. One scan (cheap: refreshed caches are fresh, others read from disk).
    forest = scan_forest(sessions_dir, summary_max_chars=summary_max_chars)
    node_by_sid = {n.sid: n for n in forest.nodes}

    # 3. Per-session detail pages for the refreshed sessions, with their node.
    for sid in refresh:
        sd = sessions_dir / sid
        if not sd.is_dir():
            continue
        detail = build_session_detail(sd, full_output_max_chars=full_output_max_chars)
        node = node_by_sid.get(sid)
        (sd / "session.html").write_text(
            render_session_html(detail, node.to_dict() if node else None),
            encoding="utf-8")

    # 4. Global timeline (always).
    timeline_path = sessions_dir / "timeline.html"
    timeline_path.write_text(render_timeline_html(forest), encoding="utf-8")
    return timeline_path
