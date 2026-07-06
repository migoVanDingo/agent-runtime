"""Tests for the timeline builtin plugin + regenerate (0027 phase d)."""
from __future__ import annotations

import json
from pathlib import Path

from arc.plugins.timeline.plugin import TimelinePlugin, regenerate


def _write_min_session(sessions: Path, sid: str, *, ended=True, meta_extra=None):
    d = sessions / sid
    d.mkdir(parents=True)
    evs = [
        {"type": "session.started", "payload": {"provider": "gemini", "model": "m1"}, "content": {}, "ts": "2026-07-06T00:00:00.000000+00:00"},
        {"type": "turn.started", "payload": {"turn_id": "T0"}, "content": {"user_input": "hi"}, "ts": "2026-07-06T00:00:01.000000+00:00"},
        {"type": "llm.call.completed", "payload": {"input_tokens": 10, "output_tokens": 2}, "content": {"response_content": [{"type": "text", "text": "yo"}]}, "ts": "2026-07-06T00:00:02.000000+00:00"},
        {"type": "turn.ended", "payload": {"turn_id": "T0"}, "content": {}, "ts": "2026-07-06T00:00:03.000000+00:00"},
    ]
    if ended:
        evs.append({"type": "session.ended", "payload": {}, "content": {}, "ts": "2026-07-06T00:00:04.000000+00:00"})
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n")
    meta = {"session_id": sid, "provider": "gemini", "model": "m1",
            "started_at": "2026-07-06T00:00:00.000000+00:00",
            "ended_at": "2026-07-06T00:00:04.000000+00:00" if ended else None}
    if meta_extra:
        meta.update(meta_extra)
    (d / "meta.json").write_text(json.dumps(meta))
    return d


def test_on_session_end_regenerates_artifacts(tmp_path):
    s = tmp_path / "sessions"
    _write_min_session(s, "SES_A")

    plugin = TimelinePlugin(sessions_dir=s, session_id="SES_A")
    plugin.on_session_end(ctx=None, outcome=None)

    assert (s / "timeline.html").is_file()
    assert (s / "SES_A" / "session.html").is_file()
    assert (s / "SES_A" / "timeline.node.json").is_file()
    # the just-ended session appears in the forest
    assert "SES_A" in (s / "timeline.html").read_text()


def test_regenerate_only_reparses_the_ended_session(tmp_path):
    s = tmp_path / "sessions"
    _write_min_session(s, "SES_OLD")
    _write_min_session(s, "SES_NEW")
    # Pre-seed a stale cache for OLD; a just_ended=NEW rebuild must NOT touch it.
    (s / "SES_OLD" / "timeline.node.json").write_text(json.dumps({
        "provider": "stale", "model": "stale", "turn_count": 42,
        "input_tokens": 0, "output_tokens": 0, "status": "completed", "turns": []}))

    regenerate(s, just_ended="SES_NEW")

    old_cache = json.loads((s / "SES_OLD" / "timeline.node.json").read_text())
    assert old_cache["turn_count"] == 42  # untouched — read from cache, not reparsed
    new_cache = json.loads((s / "SES_NEW" / "timeline.node.json").read_text())
    assert new_cache["turn_count"] == 1   # freshly built


def test_rebuild_all_refreshes_every_session(tmp_path):
    s = tmp_path / "sessions"
    _write_min_session(s, "SES_A")
    (s / "SES_A" / "timeline.node.json").write_text(json.dumps({"turn_count": 99}))

    regenerate(s, rebuild_all=True)
    cache = json.loads((s / "SES_A" / "timeline.node.json").read_text())
    assert cache["turn_count"] == 1  # rebuild_all reparsed it


def test_branch_edge_survives_via_event_when_meta_lacks_lineage(tmp_path):
    # Mirrors the on_session_end race: recorder clobbers lineage before the
    # TUI re-stamps. The scanner's event fallback must still draw the edge.
    s = tmp_path / "sessions"
    _write_min_session(s, "SES_ROOT")
    d = _write_min_session(s, "SES_BRANCH")  # meta has NO lineage
    # append a session.branched event (as the TUI emits at branch birth)
    with (d / "events.jsonl").open("a") as f:
        f.write(json.dumps({"type": "session.branched", "payload": {
            "source_session_id": "SES_ROOT", "branched_at_turn": 1,
            "restored_message_count": 2}, "content": {}, "ts": "2026-07-06T00:00:05.000000+00:00"}) + "\n")

    regenerate(s, rebuild_all=True)
    html = (s / "timeline.html").read_text()
    data = json.loads(html.split('id="forest-data">')[1].split("</script>")[0].replace("<\\/", "</"))
    branch_edges = [e for e in data["edges"] if e["child_sid"] == "SES_BRANCH"]
    assert branch_edges and branch_edges[0]["kind"] == "branch"


def test_regenerate_no_sessions(tmp_path):
    s = tmp_path / "sessions"
    s.mkdir()
    p = regenerate(s)
    assert p.is_file()
    assert "no sessions recorded yet" in p.read_text()
