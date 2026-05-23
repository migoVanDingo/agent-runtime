"""Tests for the JSONL recorder plugin.

End-to-end coverage: wire a recorder into a real AgentSession with a fake
provider, run a turn, verify the events.jsonl content + meta.json + index.jsonl
look right and are JSON-loadable line-by-line.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from arc.config import (
    BootstrapConfig,
    Config,
    PluginEntry,
    PluginsConfig,
    ProviderConfig,
    RetryConfig,
    RuntimeConfig,
    ToolsConfig,
    TUIConfig,
)
from arc.plugins import BuiltPlugin, PluginBuildContext, build as build_plugins
from arc.plugins.jsonl_recorder import JSONLRecorder
from arc.runtime.bus import EventBus, HookRegistry
from arc.runtime.events import EventType
from arc.runtime.hooks import ContentBlock, LLMRequest, LLMResponse, SessionContext, TurnOutcome
from arc.runtime.loop import AgentSession
from arc.tools.base import ToolRegistry


# ── Helpers ────────────────────────────────────────────────────────────────


def _cfg(plugins_enabled: list[PluginEntry] | None = None) -> Config:
    return Config(
        runtime=RuntimeConfig(
            workspace=".", max_iterations=10, max_tool_calls_per_turn=5,
            show_thinking=True, log_level="info",
            system_prompt="be concise",
            iteration_cap_message="wrap up", tool_call_cap_message="wrap up",
            cycle_detection_threshold=3, cycle_detected_message="cycle stop",
        ),
        provider=ProviderConfig(
            name="fake", model="fake-1", api_key_env="FAKE_KEY", base_url=None,
            timeout_seconds=10.0,
            retry=RetryConfig(max_attempts=1, backoff_base_seconds=0.01, backoff_max_seconds=0.05),
            params={},
        ),
        tools=ToolsConfig(enabled=[], config={}),
        plugins=PluginsConfig(
            failure_threshold=3, exception_message_max_chars=500,
            enabled=plugins_enabled or [],
        ),
        tui=TUIConfig(enabled=False, theme="default", inline_mode=True,
                      spinner_style="dots", prompt_prefix="❯ ",
                      show_token_counts=True, show_event_count=False,
                      show_thinking=True, tool_output_max_lines=30,
                      toolbar_enabled=True, input_history_enabled=True),
        bootstrap=BootstrapConfig(create_workspace_dir=False, write_example_session=False),
        source_path=None,  # type: ignore[arg-type]
    )


class FakeProvider:
    name = "fake"
    def __init__(self, responses):
        self._q = list(responses)
    def chat(self, req):
        return self._q.pop(0)


def _build_session_with_recorder(tmp_path: Path, provider, session_id: str = "Ses_test"):
    """Wire a session with the JSONL recorder registered on all three hooks."""
    cfg = _cfg()
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)

    recorder = JSONLRecorder(
        sessions_dir=tmp_path,
        session_id=session_id,
        config_snapshot_yaml="# snapshot for replay\n",
    )
    registry.register(recorder, hooks_order={
        "on_session_start": 10,
        "on_event": 100,
        "on_session_end": 10,
    })

    sess = AgentSession(
        config=cfg, provider=provider, tools=ToolRegistry(),
        registry=registry, bus=bus, session_id=session_id,
    )
    return sess, recorder


# ── Lifecycle: session_start creates layout ───────────────────────────────


def test_on_session_start_creates_dir_meta_and_snapshot(tmp_path):
    sess, recorder = _build_session_with_recorder(tmp_path, FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="hi")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
    ]))
    sess.start()

    session_dir = tmp_path / "Ses_test"
    assert session_dir.is_dir()
    assert (session_dir / "meta.json").is_file()
    assert (session_dir / "config.snapshot.yml").is_file()
    assert (session_dir / "events.jsonl").is_file()

    meta = json.loads((session_dir / "meta.json").read_text())
    assert meta["session_id"] == "Ses_test"
    assert meta["ended_at"] is None
    assert meta["provider"] == "fake"
    assert meta["model"] == "fake-1"

    assert "# snapshot for replay" in (session_dir / "config.snapshot.yml").read_text()


# ── on_event: every event appended as canonical JSON line ─────────────────


def test_on_event_appends_canonical_json_lines(tmp_path):
    sess, recorder = _build_session_with_recorder(tmp_path, FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="done")],
                    stop_reason="end_turn", input_tokens=2, output_tokens=2, raw={}),
    ]))

    sess.run_turn("hello")
    sess.end()

    events_path = tmp_path / "Ses_test" / "events.jsonl"
    lines = events_path.read_text().splitlines()
    assert len(lines) >= 6  # session.started + turn.started + 2 llm + turn.ended + session.ended

    # Each line is one valid JSON object
    parsed = [json.loads(line) for line in lines]
    types = [e["type"] for e in parsed]
    assert EventType.SESSION_STARTED in types
    assert EventType.TURN_STARTED in types
    assert EventType.LLM_CALL_STARTED in types
    assert EventType.LLM_CALL_COMPLETED in types
    assert EventType.TURN_ENDED in types
    assert EventType.SESSION_ENDED in types


def test_recorded_lines_use_canonical_separators(tmp_path):
    """Replay correctness depends on compact, deterministic JSON."""
    sess, _ = _build_session_with_recorder(tmp_path, FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="x")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
    ]))
    sess.run_turn("hi")
    sess.end()

    line = (tmp_path / "Ses_test" / "events.jsonl").read_text().splitlines()[0]
    # Compact separators — no spaces around : or , in keys
    assert ": " not in line  # would indicate pretty-printed
    assert ", " not in line


# ── Field order preserved ─────────────────────────────────────────────────


def test_recorded_envelope_field_order_matches_spec(tmp_path):
    """§6.1 specifies the envelope order. The recorder must preserve it."""
    sess, _ = _build_session_with_recorder(tmp_path, FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="x")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
    ]))
    sess.run_turn("hi")
    sess.end()

    line = (tmp_path / "Ses_test" / "events.jsonl").read_text().splitlines()[0]
    parsed = json.loads(line, object_pairs_hook=list)
    keys = [k for k, _ in parsed]
    expected = [
        "event_id", "session_id", "turn_id", "scope", "parent_event_id",
        "ts", "ts_monotonic_ns", "type", "stage", "severity", "duration_ms",
        "payload", "content", "schema_version",
    ]
    assert keys == expected


# ── Causation chain preserved ──────────────────────────────────────────────


def test_parent_event_chain_persists_to_disk(tmp_path):
    """tool.call.* events should be parented to the llm.call.started that requested them."""
    class EchoTool:
        name = "echo"
        description = "echo"
        @property
        def input_schema(self):
            from arc.tools.base import ToolInputSchema
            return ToolInputSchema(properties={"text": {"type": "string"}}, required=["text"])
        def execute(self, input):
            return input.get("text", "")

    provider = FakeProvider([
        LLMResponse(content=[ContentBlock(type="tool_use", tool_use_id="x",
                                          tool_name="echo", tool_input={"text": "y"})],
                    stop_reason="tool_use", input_tokens=1, output_tokens=1, raw={}),
        LLMResponse(content=[ContentBlock(type="text", text="done")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
    ])
    cfg = _cfg()
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)
    tool_reg = ToolRegistry()
    tool_reg.register(EchoTool())
    recorder = JSONLRecorder(sessions_dir=tmp_path, session_id="Ses_chain")
    registry.register(recorder, hooks_order={
        "on_session_start": 10, "on_event": 100, "on_session_end": 10,
    })
    sess = AgentSession(config=cfg, provider=provider, tools=tool_reg,
                        registry=registry, bus=bus, session_id="Ses_chain")
    sess.run_turn("call the tool")
    sess.end()

    events = [json.loads(l) for l in
              (tmp_path / "Ses_chain" / "events.jsonl").read_text().splitlines()]
    llm_started = next(e for e in events if e["type"] == EventType.LLM_CALL_STARTED)
    tool_started = next(e for e in events if e["type"] == EventType.TOOL_CALL_STARTED)
    assert tool_started["parent_event_id"] == llm_started["event_id"]


# ── on_session_end: meta updated + index appended ─────────────────────────


def test_on_session_end_stamps_meta_and_appends_index(tmp_path):
    sess, _ = _build_session_with_recorder(tmp_path, FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="done")],
                    stop_reason="end_turn", input_tokens=2, output_tokens=2, raw={}),
    ]))
    sess.run_turn("hi")
    sess.end()

    meta = json.loads((tmp_path / "Ses_test" / "meta.json").read_text())
    assert meta["ended_at"] is not None
    assert meta["last_outcome"]["success"] is True
    assert meta["last_outcome"]["n_llm_calls"] == 1

    index_path = tmp_path / "index.jsonl"
    assert index_path.is_file()
    index_lines = index_path.read_text().splitlines()
    assert len(index_lines) == 1
    entry = json.loads(index_lines[0])
    assert entry["session_id"] == "Ses_test"
    assert entry["provider"] == "fake"


# ── Plugin factory wiring ─────────────────────────────────────────────────


def test_plugin_factory_builds_jsonl_recorder(tmp_path):
    entries = [PluginEntry(
        name="jsonl-recorder", enabled=True, config={},
        hooks_order={"on_event": 100},
    )]
    plugins_cfg = PluginsConfig(failure_threshold=3, exception_message_max_chars=500,
                                 enabled=entries)
    built = build_plugins(plugins_cfg, PluginBuildContext(
        sessions_dir=tmp_path, session_id="Ses_factory", config_snapshot_yaml=None,
    ))
    assert len(built) == 1
    assert built[0].name == "jsonl-recorder"
    assert isinstance(built[0].instance, JSONLRecorder)
    assert built[0].hooks_order == {"on_event": 100}


def test_plugin_factory_skips_disabled(tmp_path):
    entries = [
        PluginEntry(name="jsonl-recorder", enabled=False, config={}, hooks_order={}),
    ]
    plugins_cfg = PluginsConfig(failure_threshold=3, exception_message_max_chars=500,
                                 enabled=entries)
    built = build_plugins(plugins_cfg, PluginBuildContext(
        sessions_dir=tmp_path, session_id="Ses_x"))
    assert built == []


def test_plugin_factory_unknown_name_raises(tmp_path):
    entries = [PluginEntry(name="invented", enabled=True, config={}, hooks_order={})]
    plugins_cfg = PluginsConfig(failure_threshold=3, exception_message_max_chars=500,
                                 enabled=entries)
    with pytest.raises(ValueError, match="unknown plugin 'invented'"):
        build_plugins(plugins_cfg, PluginBuildContext(
            sessions_dir=tmp_path, session_id="Ses_x"))
