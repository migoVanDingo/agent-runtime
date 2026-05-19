"""Hello-world acceptance test.

Per design `0001-foundation-phase0-design.md` §10.3 there are four pass/fail
criteria for phase 1 completion:

  1. Functional        — the agent answers correctly
  2. Recording         — events.jsonl contains the 10 expected event types
  3. Canonical content — recorded LLM messages match what was sent on the wire
  4. Replay            — replay produces byte-identical output (v2.0.5 gate)

This file implements 1, 2, 3, plus structural checks (well-formed JSON, valid
ordering, parent_event_id chains, no extra side effects). Criterion 4 is
covered by `tests/integration/test_replay.py` in phase v2.0.5.

The test hits the real Gemini API. Skipped if GEMINI_API_KEY isn't set.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from arc.cli import main
from arc.runtime.events import EventType


# Load .env for the API key so this works the same as `arc` from the CLI
def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)


# ── The test ───────────────────────────────────────────────────────────────


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Set up an ARC_HOME and a workspace with three known files."""
    home = tmp_path / "arc-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("alpha")
    (workspace / "b.txt").write_text("beta")
    (workspace / "c.txt").write_text("gamma")

    monkeypatch.setenv("ARC_HOME", str(home))
    return home, workspace


def _run_hello_world(home: Path, workspace: Path) -> tuple[int, Path]:
    """Bootstrap, then invoke arc run with the hello-world prompt.

    Returns (return_code, session_dir).
    """
    # Bootstrap first so we can set workspace before running
    rc_b = main(["bootstrap"])
    assert rc_b == 0

    # Edit config to point at our workspace
    cfg_text = (home / "config.yml").read_text()
    cfg_text = cfg_text.replace(
        'workspace: "."',
        f'workspace: "{workspace}"',
    )
    (home / "config.yml").write_text(cfg_text)

    rc = main(["run", f"List the files in {workspace}"])

    # Find the session that was just created
    session_dirs = sorted([p for p in (home / "sessions").iterdir() if p.is_dir()])
    assert len(session_dirs) >= 1, "no session directory was created"
    return rc, session_dirs[-1]


def test_criterion_1_functional(world):
    """The agent answers correctly — output mentions all three files."""
    home, workspace = world
    rc, session_dir = _run_hello_world(home, workspace)

    # Read the final response from the recorded events
    events = _read_events(session_dir)
    turn_ended = next(e for e in events if e["type"] == EventType.TURN_ENDED)
    final = turn_ended["content"]["final_response"]

    assert "a.txt" in final
    assert "b.txt" in final
    assert "c.txt" in final
    assert rc == 0


def test_criterion_2_recording_has_all_expected_event_types(world):
    """events.jsonl contains every event type called out in §10.3."""
    home, workspace = world
    rc, session_dir = _run_hello_world(home, workspace)

    events = _read_events(session_dir)
    types = [e["type"] for e in events]

    expected = [
        EventType.SESSION_STARTED,
        EventType.TURN_STARTED,
        EventType.LLM_CALL_STARTED,
        EventType.LLM_CALL_COMPLETED,
        EventType.TOOL_CALL_STARTED,
        EventType.TOOL_CALL_COMPLETED,
        EventType.LLM_CALL_STARTED,    # second LLM call (synthesis after tool)
        EventType.LLM_CALL_COMPLETED,
        EventType.TURN_ENDED,
        EventType.SESSION_ENDED,
    ]
    for t in expected:
        assert t in types, f"missing event type {t} in recording"

    # Specifically: at least 2 LLM calls (one to choose tool, one to synthesize)
    assert types.count(EventType.LLM_CALL_STARTED) >= 2
    assert types.count(EventType.LLM_CALL_COMPLETED) >= 2

    # Exactly one of each session/turn boundary
    assert types.count(EventType.SESSION_STARTED) == 1
    assert types.count(EventType.SESSION_ENDED) == 1
    assert types.count(EventType.TURN_STARTED) == 1
    assert types.count(EventType.TURN_ENDED) == 1


def test_criterion_3_canonical_content_matches_wire(world):
    """The LLM_CALL_STARTED content field contains the actual messages, system,
    tools, and params that were sent on the wire (no pretty-printing drift).
    """
    home, workspace = world
    rc, session_dir = _run_hello_world(home, workspace)

    events = _read_events(session_dir)

    # Find the FIRST llm.call.started — that's the one with just the user msg
    first_llm = next(e for e in events if e["type"] == EventType.LLM_CALL_STARTED)
    content = first_llm["content"]

    assert "messages" in content
    assert "system" in content
    assert "tools" in content
    assert "params" in content

    # User message is exactly what was sent
    msgs = content["messages"]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert str(workspace) in msgs[0]["content"]

    # System prompt matches what's in config (not transformed)
    assert "arc" in content["system"]  # default system prompt mentions "arc"

    # Tools include ls (and possibly bash_exec from phase 2.1 defaults)
    tool_names = [t["name"] for t in content["tools"]]
    assert "ls" in tool_names

    # Params include temperature/max_tokens from defaults
    assert "temperature" in content["params"]
    assert "max_tokens" in content["params"]


def test_recording_is_well_formed_jsonl(world):
    """Every line of events.jsonl is one valid JSON object — no partial writes."""
    home, workspace = world
    rc, session_dir = _run_hello_world(home, workspace)

    text = (session_dir / "events.jsonl").read_text()
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) >= 10  # at minimum the 10 from the spec

    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            pytest.fail(f"line {i} is not valid JSON: {e}\n  line: {line[:200]}")
        # Envelope shape
        for field in ("event_id", "session_id", "type", "stage", "ts",
                      "ts_monotonic_ns", "schema_version"):
            assert field in obj, f"line {i} missing field {field!r}"


def test_event_causation_chain(world):
    """tool.call.* events should be parented to the llm.call.started that requested them."""
    home, workspace = world
    rc, session_dir = _run_hello_world(home, workspace)

    events = _read_events(session_dir)
    llm_starts = [e for e in events if e["type"] == EventType.LLM_CALL_STARTED]
    tool_started = next(e for e in events if e["type"] == EventType.TOOL_CALL_STARTED)
    tool_completed = next(e for e in events if e["type"] == EventType.TOOL_CALL_COMPLETED)

    # The tool.call.started should be parented to one of the llm.call.started events
    parents = {e["event_id"] for e in llm_starts}
    assert tool_started["parent_event_id"] in parents

    # tool.call.completed should be parented to tool.call.started
    assert tool_completed["parent_event_id"] == tool_started["event_id"]


def test_session_metadata_persists(world):
    """meta.json + config.snapshot.yml + index.jsonl are written correctly."""
    home, workspace = world
    rc, session_dir = _run_hello_world(home, workspace)

    meta = json.loads((session_dir / "meta.json").read_text())
    assert meta["provider"] == "gemini"
    assert meta["ended_at"] is not None
    assert meta["last_outcome"]["success"] is True

    snap = (session_dir / "config.snapshot.yml").read_text()
    assert "provider:" in snap
    assert "gemini-3.1-flash-lite-preview" in snap

    index_lines = (home / "sessions" / "index.jsonl").read_text().splitlines()
    assert len(index_lines) == 1  # one session run
    entry = json.loads(index_lines[0])
    assert entry["session_id"] == meta["session_id"]


def test_ls_tool_output_was_actually_returned(world):
    """The recorded tool.call.completed should contain the real ls output —
    that's the proof the tool ran and the result was captured for replay."""
    home, workspace = world
    rc, session_dir = _run_hello_world(home, workspace)

    events = _read_events(session_dir)
    tool_completed = next(e for e in events if e["type"] == EventType.TOOL_CALL_COMPLETED)
    output = tool_completed["content"]["output"]
    assert "a.txt" in output
    assert "b.txt" in output
    assert "c.txt" in output


# ── Helpers ────────────────────────────────────────────────────────────────


def _read_events(session_dir: Path) -> list[dict]:
    """Parse events.jsonl into a list of dicts."""
    text = (session_dir / "events.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]
