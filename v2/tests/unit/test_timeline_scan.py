"""Golden tests for the 0027 timeline scanner + summarizer (phase a).

Builds fixture session dirs on disk (events.jsonl + meta.json) covering every
edge kind, orphans, the event-lineage fallback, and node-cache reuse, then
asserts the resulting Forest shape.
"""
from __future__ import annotations

import json
from pathlib import Path

from arc.timeline.scan import scan_forest
from arc.timeline.summarize import build_node_cache, load_or_build_node_cache


# ── fixture builders ──────────────────────────────────────────────────────


def _ev(t, *, payload=None, content=None, ts="2026-07-06T00:00:00.000000+00:00"):
    return {"type": t, "payload": payload or {}, "content": content or {}, "ts": ts}


def _write_session(sessions: Path, sid, *, provider="gemini", model="m1",
                   turns=(), meta_extra=None, ended=True, events_extra=()):
    """turns: list of (user_input, assistant_text, n_tool_calls, in_tok, out_tok)."""
    d = sessions / sid
    d.mkdir(parents=True)
    evs = [_ev("session.started", payload={"provider": provider, "model": model})]
    for i, (ui, at, ntc, it, ot) in enumerate(turns):
        evs.append(_ev("turn.started", payload={"turn_id": f"T{i}"}, content={"user_input": ui}))
        for _ in range(ntc):
            evs.append(_ev("tool.call.started", payload={"tool_name": "briefbot_search"}))
        evs.append(_ev("llm.call.completed",
                       payload={"input_tokens": it, "output_tokens": ot, "stop_reason": "end_turn"},
                       content={"response_content": [{"type": "text", "text": at}]}))
        evs.append(_ev("turn.ended", payload={"turn_id": f"T{i}"}))
    evs.extend(events_extra)
    if ended:
        evs.append(_ev("session.ended", payload={"n_messages": len(turns) * 2}))

    (d / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")

    meta = {
        "session_id": sid, "provider": provider, "model": model,
        "started_at": f"2026-07-06T00:00:0{len(list(sessions.iterdir()))}.000000+00:00",
        "ended_at": "2026-07-06T01:00:00.000000+00:00" if ended else None,
    }
    if meta_extra:
        meta.update(meta_extra)
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


# ── summarizer ─────────────────────────────────────────────────────────────


def test_node_cache_reads_correct_field_locations(tmp_path):
    s = tmp_path / "sessions"
    d = _write_session(s, "SES_A", turns=[
        ("what are llms", "LLMs are models", 2, 100, 20),
        ("more detail", "Here is more", 0, 50, 10),
    ])
    cache = build_node_cache(d)
    assert cache["turn_count"] == 2
    assert cache["input_tokens"] == 150
    assert cache["output_tokens"] == 30
    assert cache["provider"] == "gemini"
    # user_input lives in content, response text in content.response_content —
    # both must survive into the summary (the compare.py bug this avoids)
    assert cache["turns"][0]["user_summary"] == "what are llms"
    assert cache["turns"][0]["assistant_summary"] == "LLMs are models"
    assert cache["turns"][0]["tool_calls"] == 2
    assert cache["status"] == "completed"


def test_summary_truncation():
    from arc.timeline.summarize import _short
    assert _short("a b   c", 40) == "a b c"           # whitespace collapse
    assert _short("x" * 100, 10) == "x" * 9 + "…"      # cap + ellipsis


def test_running_and_empty_status(tmp_path):
    s = tmp_path / "sessions"
    live = _write_session(s, "SES_LIVE", turns=[("hi", "yo", 0, 1, 1)], ended=False)
    empty = _write_session(s, "SES_EMPTY", turns=[], ended=False)
    assert build_node_cache(live)["status"] == "running"
    assert build_node_cache(empty)["status"] == "empty"


def test_node_cache_roundtrip_and_reuse(tmp_path):
    s = tmp_path / "sessions"
    d = _write_session(s, "SES_A", turns=[("q", "a", 1, 5, 5)])
    # write a deliberately-wrong cache; load_or_build must trust it (reuse)
    (d / "timeline.node.json").write_text(json.dumps({"turn_count": 99}))
    assert load_or_build_node_cache(d)["turn_count"] == 99
    # corrupt cache → rebuild from events
    (d / "timeline.node.json").write_text("{not json")
    assert load_or_build_node_cache(d)["turn_count"] == 1


# ── scanner / forest ─────────────────────────────────────────────────────────


def test_forest_all_edge_kinds(tmp_path):
    s = tmp_path / "sessions"
    _write_session(s, "SES_ROOT", turns=[("a", "b", 0, 1, 1), ("c", "d", 0, 1, 1)])
    _write_session(s, "SES_BRANCH", turns=[("x", "y", 0, 1, 1)],
                   meta_extra={"resumed_from": "SES_ROOT", "branched_at_turn": 1})
    _write_session(s, "SES_RETRY", turns=[("x", "y", 0, 1, 1)],
                   meta_extra={"resumed_from": "SES_ROOT", "branched_at_turn": 2,
                               "retry_of_turn": 2})
    _write_session(s, "SES_RESUME", turns=[("x", "y", 0, 1, 1)],
                   meta_extra={"resumed_from": "SES_ROOT"})
    _write_session(s, "SES_REPLAY", turns=[("a", "b", 0, 1, 1)],
                   meta_extra={"replay_of": "SES_ROOT", "replay_mode": "by_call"})
    _write_session(s, "SES_RERUN", turns=[("a", "b", 0, 1, 1)],
                   meta_extra={"rerun_of": "SES_ROOT"})

    forest = scan_forest(s)
    kinds = {(e.child_sid): (e.kind, e.parent_turn) for e in forest.edges}
    assert kinds["SES_BRANCH"] == ("branch", 1)
    assert kinds["SES_RETRY"] == ("retry", 2)
    assert kinds["SES_RESUME"] == ("resume", None)
    assert kinds["SES_REPLAY"] == ("replay", None)
    assert kinds["SES_RERUN"] == ("rerun", None)
    assert all(e.parent_sid == "SES_ROOT" for e in forest.edges)
    assert forest.roots == ["SES_ROOT"]  # every other session has a present parent


def test_orphan_renders_as_root_with_flag(tmp_path):
    s = tmp_path / "sessions"
    _write_session(s, "SES_ORPHAN", turns=[("x", "y", 0, 1, 1)],
                   meta_extra={"resumed_from": "SES_GONE", "branched_at_turn": 1})
    forest = scan_forest(s)
    orphan = next(n for n in forest.nodes if n.sid == "SES_ORPHAN")
    assert orphan.parent_missing is True
    assert "SES_ORPHAN" in forest.roots  # no lane to attach to → it's a root


def test_lineage_fallback_from_event_when_meta_missing(tmp_path):
    # Hard-killed branch: meta has no lineage, but session.branched persists it.
    s = tmp_path / "sessions"
    _write_session(s, "SES_ROOT", turns=[("a", "b", 0, 1, 1)])
    branch_ev = _ev("session.branched", payload={
        "source_session_id": "SES_ROOT", "branched_at_turn": 1,
        "restored_message_count": 2})
    _write_session(s, "SES_KILLED", turns=[("x", "y", 0, 1, 1)],
                   ended=False, events_extra=[branch_ev])  # meta_extra omitted

    forest = scan_forest(s)
    edge = next(e for e in forest.edges if e.child_sid == "SES_KILLED")
    assert edge.kind == "branch"
    assert edge.parent_sid == "SES_ROOT"
    assert edge.parent_turn == 1
    killed = next(n for n in forest.nodes if n.sid == "SES_KILLED")
    assert killed.status == "running"  # no session.ended


def test_empty_sessions_dir(tmp_path):
    assert scan_forest(tmp_path / "nope").to_dict() == {"nodes": [], "edges": [], "roots": []}


def test_forest_serializable(tmp_path):
    s = tmp_path / "sessions"
    _write_session(s, "SES_ROOT", turns=[("a", "b", 1, 5, 5)])
    d = scan_forest(s).to_dict()
    assert json.loads(json.dumps(d))  # round-trips through JSON
    assert d["nodes"][0]["turns"][0]["user_summary"] == "a"
