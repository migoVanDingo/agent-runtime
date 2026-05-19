"""Tests for the inline TUI.

Uses an injected prompt_fn to drive input — no real TTY needed. The Rich
console is given a StringIO so we can assert on rendered text.
"""
from __future__ import annotations

import io
from collections import deque

import pytest
from rich.console import Console

from arc.config import (
    BootstrapConfig, Config, PluginsConfig, ProviderConfig, RetryConfig,
    RuntimeConfig, ToolsConfig, TUIConfig,
)
from arc.runtime.bus import EventBus, HookRegistry
from arc.runtime.hooks import ContentBlock, LLMResponse
from arc.runtime.loop import AgentSession
from arc.tools.base import ToolInputSchema, ToolRegistry
from arc.tui.app import TUIApp


# ── Fixtures ──────────────────────────────────────────────────────────────


def _cfg() -> Config:
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
        plugins=PluginsConfig(failure_threshold=3, exception_message_max_chars=500, enabled=[]),
        tui=TUIConfig(
            enabled=True, theme="default", inline_mode=True,
            spinner_style="dots", prompt_prefix="❯ ",
            show_token_counts=True, show_event_count=False,
        ),
        bootstrap=BootstrapConfig(create_workspace_dir=False, write_example_session=False),
        source_path=None,  # type: ignore[arg-type]
    )


class FakeProvider:
    name = "fake"
    def __init__(self, responses):
        self._q = deque(responses)
    def chat(self, req):
        return self._q.popleft()


class EchoTool:
    name = "echo"
    description = "echo"
    @property
    def input_schema(self):
        return ToolInputSchema(properties={"text": {"type": "string"}}, required=["text"])
    def execute(self, input):
        return input.get("text", "")


def _build_app(inputs: list[str], provider, tools=None) -> tuple[TUIApp, io.StringIO]:
    """Wire up a TUIApp with an injected prompt_fn that pops from `inputs`.
    Returns (app, output_buffer).
    """
    out = io.StringIO()
    console = Console(file=out, force_terminal=False, width=120, color_system=None)
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)
    tool_reg = ToolRegistry()
    for t in tools or []:
        tool_reg.register(t)
    sess = AgentSession(
        config=_cfg(), provider=provider, tools=tool_reg,
        registry=registry, bus=bus, session_id="SES_test",
    )

    queue = deque(inputs)

    def prompt_fn(prefix: str) -> str:
        if not queue:
            raise EOFError  # natural exit when inputs exhausted
        return queue.popleft()

    app = TUIApp(
        config=_cfg(),
        session=sess,
        home_display="/tmp/test-home",
        prompt_fn=prompt_fn,
        console=console,
    )
    return app, out


# ── Basic flow ────────────────────────────────────────────────────────────


def test_banner_prints_at_startup():
    provider = FakeProvider([])
    app, out = _build_app([], provider)
    app.run()
    text = out.getvalue()
    # ASCII-art logo: a recognizable glyph from the wordmark and the v2 tag
    assert "█████" in text
    assert "v2" in text
    # Session info
    assert "fake / fake-1" in text
    assert "SES_test" in text
    assert "/tmp/test-home" in text


def test_single_turn_renders_response():
    provider = FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="hello back")],
                    stop_reason="end_turn", input_tokens=5, output_tokens=3, raw={}),
    ])
    app, out = _build_app(["hi"], provider)
    app.run()
    text = out.getvalue()
    assert "hi" in text                # echoed user input
    assert "hello back" in text        # assistant response


def test_multiple_turns_accumulate():
    provider = FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="first reply")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
        LLMResponse(content=[ContentBlock(type="text", text="second reply")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
    ])
    app, out = _build_app(["one", "two"], provider)
    app.run()
    text = out.getvalue()
    assert "first reply" in text
    assert "second reply" in text


def test_tool_call_and_result_render():
    provider = FakeProvider([
        LLMResponse(
            content=[ContentBlock(type="tool_use", tool_use_id="x",
                                  tool_name="echo", tool_input={"text": "hi"})],
            stop_reason="tool_use", input_tokens=1, output_tokens=1, raw={},
        ),
        LLMResponse(
            content=[ContentBlock(type="text", text="echoed")],
            stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={},
        ),
    ])
    app, out = _build_app(["echo hi"], provider, tools=[EchoTool()])
    app.run()
    text = out.getvalue()
    assert "echo(" in text             # tool call line
    assert "hi" in text                 # tool input
    assert "echoed" in text             # final assistant reply


def test_token_count_in_footer():
    provider = FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="hi")],
                    stop_reason="end_turn", input_tokens=42, output_tokens=7, raw={}),
    ])
    app, out = _build_app(["x"], provider)
    app.run()
    text = out.getvalue()
    assert "42/7" in text


# ── Slash commands ────────────────────────────────────────────────────────


def test_help_command():
    app, out = _build_app(["/help"], FakeProvider([]))
    app.run()
    text = out.getvalue()
    assert "commands:" in text
    assert "/exit" in text


def test_exit_command_ends_session():
    """/exit should terminate the loop without calling the provider."""
    provider = FakeProvider([])  # empty → would crash if a turn ran
    app, out = _build_app(["/exit"], provider)
    rc = app.run()
    assert rc == 0


def test_quit_command_ends_session():
    provider = FakeProvider([])
    app, out = _build_app(["/quit"], provider)
    rc = app.run()
    assert rc == 0


def test_unknown_slash_command_prints_error():
    app, out = _build_app(["/banana", "/exit"], FakeProvider([]))
    app.run()
    text = out.getvalue()
    assert "unknown command" in text
    assert "/banana" in text


# ── Edge cases ────────────────────────────────────────────────────────────


def test_empty_input_does_not_run_a_turn():
    provider = FakeProvider([])  # would crash if a turn ran
    app, out = _build_app(["", "  ", "/exit"], provider)
    rc = app.run()
    assert rc == 0


def test_eof_exits_cleanly():
    """When inputs are exhausted, prompt_fn raises EOFError and we exit."""
    app, out = _build_app([], FakeProvider([]))
    rc = app.run()
    assert rc == 0


def test_failed_llm_renders_error():
    """LLM_CALL_FAILED event → red error printed."""
    class BrokenProvider:
        name = "fake"
        def chat(self, req):
            raise RuntimeError("provider exploded")

    app, out = _build_app(["go"], BrokenProvider())
    app.run()
    text = out.getvalue()
    assert "LLM call failed" in text or "exploded" in text


def test_tui_registers_itself_as_event_plugin():
    """The TUI should be a registered plugin on `on_event` so it gets live updates."""
    provider = FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="hi")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
    ])
    app, _ = _build_app(["x"], provider)
    app.run()
    # After run, registry should have on_event subscribers including tui-app
    chain = app._session.registry._chains.get("on_event", [])
    plugin_names = [p[1] for p in chain]
    assert "tui-app" in plugin_names
