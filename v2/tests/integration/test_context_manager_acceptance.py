"""Phase 3.0 acceptance: sliding-window context manager.

Acceptance approach: build an AgentSession with `initial_messages` containing
a pre-populated long conversation, then run ONE turn against real Gemini.
The plugin's pack_context fires during the LLM call and should filter.

(Using `arc resume` chains here would defeat the test — the resume CLI
restores only messages from the immediate prior session, not the full
chain, so it never builds up enough history to trigger packing. That's
a known limitation of resume, separate from the context manager. To test
the context manager end-to-end we just inject the history directly.)

Requires GEMINI_API_KEY.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from arc.cli import main


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


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Bootstrap a fresh ARC_HOME with tight context-manager settings."""
    home = tmp_path / "arc-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setenv("ARC_HOME", str(home))
    main(["bootstrap"])

    cfg = (home / "config.yml").read_text()
    cfg = cfg.replace('workspace: "."', f'workspace: "{workspace}"')
    cfg = cfg.replace("keep_first_turns: 2", "keep_first_turns: 1")
    cfg = cfg.replace("keep_last_turns: 20", "keep_last_turns: 2")
    (home / "config.yml").write_text(cfg)
    return home, workspace


def _events(home: Path, sid: str) -> list[dict]:
    p = home / "sessions" / sid / "events.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _latest_session_id(home: Path) -> str:
    return sorted([p.name for p in (home / "sessions").iterdir() if p.is_dir()])[-1]


def _build_long_session(home: Path, workspace: Path, n_extra_turns: int) -> str:
    """Build a session with N pre-existing turns by injecting initial_messages
    into a directly-constructed AgentSession. Returns the new session_id.
    """
    from arc.bootstrap import paths_for, resolve_home
    from arc.config import load
    from arc.plugins import PluginBuildContext, build as build_plugins
    from arc.providers import build as build_provider
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession
    from arc.runtime.hooks import ContentBlock, Message
    from arc.tools import build as build_tools
    from arc.user_gate import NoOpGate

    paths = paths_for(resolve_home(None))
    cfg = load(paths.config_file)

    # Build many user/assistant turn pairs
    initial = []
    for i in range(n_extra_turns):
        initial.append(Message(role="user", content=f"prior turn {i}"))
        initial.append(Message(
            role="assistant",
            content=[ContentBlock(type="text", text=f"reply to prior turn {i}")],
        ))

    provider = build_provider(cfg.provider)
    tools = build_tools(cfg.tools)
    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)
    sid = new_session_id()
    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir,
        session_id=sid,
        config_snapshot_yaml=paths.config_file.read_text(),
        user_gate=NoOpGate(),
        bus=bus,
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)

    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=sid,
        initial_messages=initial,
    )
    try:
        sess.start()
        sess.run_turn("FINAL TURN — please respond briefly")
    finally:
        sess.end()
    return sid


# ── No-op on short conversations ────────────────────────────────────────


def test_short_conversation_does_not_trigger_packing(world):
    """1-turn conversation, threshold = 3 (1+2). No packing should fire."""
    home, workspace = world
    main(["run", f"What files are in {workspace}?"])
    sid = _latest_session_id(home)

    events = _events(home, sid)
    packed = [e for e in events if e["type"] == "runtime.context_packed"]
    assert packed == []


# ── Long conversation triggers packing ──────────────────────────────────


def test_long_conversation_triggers_packing(world):
    """Direct-injected 10-turn session → exceeds threshold (1+2=3) → packing fires."""
    home, workspace = world
    sid = _build_long_session(home, workspace, n_extra_turns=10)

    events = _events(home, sid)
    packed = [e for e in events if e["type"] == "runtime.context_packed"]
    assert len(packed) >= 1, "expected runtime.context_packed event"

    p = packed[0].get("payload", {})
    assert p["n_messages_before"] > p["n_messages_after"]
    assert p["bytes_dropped"] > 0


def test_packing_preserves_first_turn(world):
    """The original first user message must survive sliding-window pruning."""
    from arc.bootstrap import paths_for, resolve_home
    from arc.config import load
    from arc.plugins import PluginBuildContext, build as build_plugins
    from arc.providers import build as build_provider
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession
    from arc.runtime.hooks import ContentBlock, Message
    from arc.tools import build as build_tools
    from arc.user_gate import NoOpGate

    home, workspace = world
    paths = paths_for(resolve_home(None))
    cfg = load(paths.config_file)

    marker = "FIRSTTURNUNIQUEMARKER42"
    initial = [
        Message(role="user", content=f"{marker} — initial goal: count to 5"),
        Message(role="assistant",
                content=[ContentBlock(type="text", text="acknowledged goal")]),
    ]
    # Add 8 more turns of bulk
    for i in range(8):
        initial.append(Message(role="user", content=f"bulk turn {i}"))
        initial.append(Message(role="assistant",
                              content=[ContentBlock(type="text", text=f"bulk reply {i}")]))

    provider = build_provider(cfg.provider)
    tools = build_tools(cfg.tools)
    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)
    sid = new_session_id()
    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir, session_id=sid,
        config_snapshot_yaml=paths.config_file.read_text(),
        user_gate=NoOpGate(), bus=bus,
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)

    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=sid,
        initial_messages=initial,
    )
    try:
        sess.start()
        sess.run_turn("repeat my unique marker from the first message")
    finally:
        sess.end()

    # The LAST llm.call.started's content.messages is what the LLM saw
    events = _events(home, sid)
    llm_starts = [e for e in events if e["type"] == "llm.call.started"]
    assert llm_starts
    msgs = llm_starts[-1]["content"]["messages"]
    # The first message after packing should still contain the marker
    assert marker in str(msgs[0]["content"]), (
        f"first turn was lost! first kept message: {msgs[0]!r}"
    )


# ── session.log mentions packing ────────────────────────────────────────


def test_log_writer_records_context_packing(world):
    home, workspace = world
    sid = _build_long_session(home, workspace, n_extra_turns=10)
    log = (home / "sessions" / sid / "session.log").read_text()
    assert "context packed:" in log
