"""Render tests for 0027 phases b/c — embed safety, escaping, structure."""
from __future__ import annotations

import json
import re

from arc.timeline.model import Edge, Forest, SessionNode, TurnNode
from arc.timeline.render import render_session_html, render_timeline_html


def _forest():
    root = SessionNode(sid="SES_ROOT", provider="gemini", model="m1",
                       turn_count=2, input_tokens=100, output_tokens=20,
                       status="completed",
                       turns=[TurnNode(1, "hello", "hi there", 1, 50, 10),
                              TurnNode(2, "more", "sure", 0, 50, 10)])
    branch = SessionNode(sid="SES_BRANCH", provider="anthropic", model="m2",
                         turn_count=1, status="completed",
                         resumed_from="SES_ROOT", branched_at_turn=1,
                         turns=[TurnNode(1, "tangent", "ok", 0, 5, 5)])
    return Forest(nodes=[root, branch],
                  edges=[Edge("SES_ROOT", "SES_BRANCH", "branch", 1)],
                  roots=["SES_ROOT"])


def test_timeline_embeds_parseable_json_matching_forest():
    f = _forest()
    out = render_timeline_html(f)
    m = re.search(r'id="forest-data">(.*?)</script>', out, re.S)
    assert m, "forest-data script block missing"
    data = json.loads(m.group(1).replace("<\\/", "</"))
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1
    assert data["edges"][0]["kind"] == "branch"


def test_timeline_is_self_contained():
    out = render_timeline_html(_forest())
    # No external resource loads — the only http(s) URIs allowed are the SVG
    # namespace constant (never fetched). No src=, CDN link, @import, or fetch.
    assert "<style>" in out and "<script>" in out  # inline assets
    assert 'src="http' not in out and "src='http" not in out
    assert 'href="http' not in out and "@import" not in out
    assert "fetch(" not in out
    external = re.findall(r'https?://(?!www\.w3\.org)[^\s"\'<>]+', out)
    assert external == [], f"unexpected external URIs: {external}"


def test_script_close_sequence_in_tool_output_cannot_break_page():
    # A tool output containing </script> must not close the data block early.
    evil = SessionNode(sid="SES_X", turn_count=1, status="completed",
                       turns=[TurnNode(1, "</script><script>alert(1)</script>", "x", 0, 1, 1)])
    out = render_timeline_html(Forest(nodes=[evil], roots=["SES_X"]))
    # exactly one real </script> per <script> — the payload's is neutralized
    body = out.split('id="forest-data">')[1]
    payload, rest = body.split("</script>", 1)
    assert "</script>" not in payload  # the evil one was escaped to <\/script>
    assert "alert(1)" in payload or "alert(1)" in payload  # still present, just inert


def test_timeline_stats_counts():
    out = render_timeline_html(_forest())
    assert "<b>2</b> sessions" in out
    assert "<b>1</b> branches" in out


def test_empty_forest_renders_empty_state():
    out = render_timeline_html(Forest())
    assert "no sessions recorded yet" in out
    assert "<b>0</b> sessions" in out


def test_session_detail_escapes_and_structures():
    detail = {"sid": "SES_A", "turns": [
        {"index": 1, "user": "look at <b>this</b>", "assistant": "done",
         "thinking": "", "tools": [
             {"name": "bash_exec", "input": {"cmd": "ls"}, "output": "a\nb"}]},
    ]}
    out = render_session_html(detail, {"provider": "gemini", "model": "m1"})
    assert "&lt;b&gt;this&lt;/b&gt;" in out  # user HTML escaped
    assert 'id="turn-1"' in out
    assert "bash_exec" in out
    assert "&lt;b&gt;" in out  # no raw injection


def test_session_detail_anchor_per_turn():
    detail = {"sid": "S", "turns": [
        {"index": i, "user": f"q{i}", "assistant": f"a{i}", "thinking": "", "tools": []}
        for i in range(1, 4)]}
    out = render_session_html(detail)
    for i in range(1, 4):
        assert f'id="turn-{i}"' in out
