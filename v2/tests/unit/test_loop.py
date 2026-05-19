"""Tests for the AgentSession / ReAct loop.

All tests use a FakeProvider so they're deterministic, fast, and don't need
a real API key. The provider returns a scripted sequence of responses.

Coverage focuses on:
  - The right events fire in the right order
  - Hooks compose correctly
  - Caps (iteration, tool calls) trigger wrap-up
  - Tool denial path works
  - Tool error path emits tool.call.failed
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from arc.config import (
    BootstrapConfig,
    Config,
    PluginsConfig,
    ProviderConfig,
    RetryConfig,
    RuntimeConfig,
    ToolsConfig,
    TUIConfig,
)
from arc.runtime.bus import EventBus, HookRegistry
from arc.runtime.events import EventType
from arc.runtime.hooks import (
    ContentBlock,
    LLMRequest,
    LLMResponse,
    ToolCall,
    ToolDenial,
    UserInput,
)
from arc.runtime.loop import AgentSession
from arc.tools.base import ToolError, ToolInputSchema, ToolRegistry


# ── Test helpers ───────────────────────────────────────────────────────────


def _cfg(**runtime_overrides) -> Config:
    rt = dict(
        workspace=".",
        max_iterations=50,
        max_tool_calls_per_turn=30,
        show_thinking=True,
        log_level="info",
        system_prompt="be concise",
        iteration_cap_message="wrap up — iteration cap",
        tool_call_cap_message="wrap up — tool cap",
        cycle_detection_threshold=3,
        cycle_detected_message="cycle — stop",
    )
    rt.update(runtime_overrides)
    return Config(
        runtime=RuntimeConfig(**rt),
        provider=ProviderConfig(
            name="fake", model="fake-1", api_key_env="FAKE_KEY", base_url=None,
            timeout_seconds=10.0,
            retry=RetryConfig(max_attempts=1, backoff_base_seconds=0.01, backoff_max_seconds=0.05),
            params={},
        ),
        tools=ToolsConfig(enabled=[], config={}),
        plugins=PluginsConfig(
            failure_threshold=3, exception_message_max_chars=500, enabled=[],
        ),
        tui=TUIConfig(
            enabled=False, theme="default", inline_mode=True,
            spinner_style="dots", prompt_prefix="❯ ",
            show_token_counts=True, show_event_count=False,
        ),
        bootstrap=BootstrapConfig(create_workspace_dir=False, write_example_session=False),
        source_path=None,  # type: ignore[arg-type]
    )


class FakeProvider:
    """Returns a scripted sequence of responses. Each chat() pops the next one."""
    name = "fake"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._queue = list(responses)
        self.calls: list[LLMRequest] = []

    def chat(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        if not self._queue:
            # Default end-of-conversation if queue exhausted
            return LLMResponse(
                content=[ContentBlock(type="text", text="ok")],
                stop_reason="end_turn",
                input_tokens=1, output_tokens=1, raw={},
            )
        return self._queue.pop(0)


class EchoTool:
    """Stub tool: echoes its `text` input."""
    name = "echo"
    description = "echo the input text"

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            properties={"text": {"type": "string"}},
            required=["text"],
        )

    def execute(self, input: dict) -> str:
        return input.get("text", "")


class FailingTool:
    """Raises ToolError."""
    name = "boom"
    description = "always fails"

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(properties={}, required=[])

    def execute(self, input: dict) -> str:
        raise ToolError("intentional failure")


class EventRecorder:
    """on_event plugin that captures every event for assertion."""
    name = "recorder"

    def __init__(self) -> None:
        self.events = []

    def on_event(self, ctx, event):
        self.events.append(event)


def _build_session(provider, tools=None, plugins=None, **cfg_overrides):
    """Wire up an AgentSession with the given pieces."""
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)
    tool_reg = ToolRegistry()
    for t in tools or []:
        tool_reg.register(t)
    for plugin, order in plugins or []:
        registry.register(plugin, hooks_order=order)
    return AgentSession(
        config=_cfg(**cfg_overrides),
        provider=provider,
        tools=tool_reg,
        registry=registry,
        bus=bus,
    )


# ── Basic flows ────────────────────────────────────────────────────────────


def test_simple_text_only_turn():
    """No tool calls — LLM ends turn immediately with text."""
    provider = FakeProvider([
        LLMResponse(
            content=[ContentBlock(type="text", text="hello there")],
            stop_reason="end_turn",
            input_tokens=5, output_tokens=3, raw={},
        ),
    ])
    recorder = EventRecorder()
    sess = _build_session(provider, plugins=[(recorder, {"on_event": 100})])

    outcome = sess.run_turn("hi")
    sess.end()

    assert outcome.success
    assert outcome.final_response == "hello there"
    assert outcome.n_llm_calls == 1
    assert outcome.n_tool_calls == 0

    types = [e.type for e in recorder.events]
    assert types[0] == EventType.SESSION_STARTED
    assert EventType.TURN_STARTED in types
    assert EventType.LLM_CALL_STARTED in types
    assert EventType.LLM_CALL_COMPLETED in types
    assert EventType.TURN_ENDED in types
    assert EventType.SESSION_ENDED in types


def test_one_tool_call_then_text():
    """LLM calls a tool, gets result, then responds with text."""
    provider = FakeProvider([
        LLMResponse(
            content=[ContentBlock(
                type="tool_use", tool_use_id="tcl_1",
                tool_name="echo", tool_input={"text": "pong"},
            )],
            stop_reason="tool_use",
            input_tokens=5, output_tokens=3, raw={},
        ),
        LLMResponse(
            content=[ContentBlock(type="text", text="echo returned pong")],
            stop_reason="end_turn",
            input_tokens=10, output_tokens=4, raw={},
        ),
    ])
    recorder = EventRecorder()
    sess = _build_session(provider, tools=[EchoTool()],
                          plugins=[(recorder, {"on_event": 100})])

    outcome = sess.run_turn("say pong")

    assert outcome.success
    assert outcome.final_response == "echo returned pong"
    assert outcome.n_llm_calls == 2
    assert outcome.n_tool_calls == 1

    types = [e.type for e in recorder.events]
    assert EventType.TOOL_CALL_STARTED in types
    assert EventType.TOOL_CALL_COMPLETED in types
    # tool.call.started should be parented to llm.call.started
    tool_started = next(e for e in recorder.events if e.type == EventType.TOOL_CALL_STARTED)
    llm_starts = [e for e in recorder.events if e.type == EventType.LLM_CALL_STARTED]
    assert tool_started.parent_event_id == llm_starts[0].event_id


def test_tool_failure_emits_failed_event():
    provider = FakeProvider([
        LLMResponse(
            content=[ContentBlock(
                type="tool_use", tool_use_id="tcl_1",
                tool_name="boom", tool_input={},
            )],
            stop_reason="tool_use",
            input_tokens=5, output_tokens=3, raw={},
        ),
        LLMResponse(
            content=[ContentBlock(type="text", text="that failed")],
            stop_reason="end_turn",
            input_tokens=10, output_tokens=4, raw={},
        ),
    ])
    recorder = EventRecorder()
    sess = _build_session(provider, tools=[FailingTool()],
                          plugins=[(recorder, {"on_event": 100})])

    outcome = sess.run_turn("try boom")
    assert outcome.success  # the loop completes even though one tool failed
    assert outcome.n_tool_calls == 1

    types = [e.type for e in recorder.events]
    assert EventType.TOOL_CALL_FAILED in types
    failed = next(e for e in recorder.events if e.type == EventType.TOOL_CALL_FAILED)
    assert "intentional failure" in failed.payload["error_message"]


# ── Caps ────────────────────────────────────────────────────────────────────


def test_iteration_cap_forces_wrap_up():
    """If max_iterations is hit, the runtime injects a wrap-up message and
    makes one final tool-less LLM call to synthesize."""
    # Provider keeps requesting tool calls forever — but cap is low
    tool_use_resp = LLMResponse(
        content=[ContentBlock(type="tool_use", tool_use_id="x",
                              tool_name="echo", tool_input={"text": "a"})],
        stop_reason="tool_use", input_tokens=1, output_tokens=1, raw={},
    )
    final_wrap = LLMResponse(
        content=[ContentBlock(type="text", text="forced wrap-up text")],
        stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={},
    )
    # Cap at 2 iterations; supply tool-use, tool-use, then wrap-up
    provider = FakeProvider([tool_use_resp, tool_use_resp, final_wrap])

    sess = _build_session(provider, tools=[EchoTool()], max_iterations=2)
    outcome = sess.run_turn("loop forever")

    assert outcome.success
    assert outcome.final_response == "forced wrap-up text"


def test_tool_call_cap_stops_dispatch_within_iteration():
    """Tool call cap stops further dispatches in the current LLM response."""
    multi_tool = LLMResponse(
        content=[
            ContentBlock(type="tool_use", tool_use_id="a",
                         tool_name="echo", tool_input={"text": "1"}),
            ContentBlock(type="tool_use", tool_use_id="b",
                         tool_name="echo", tool_input={"text": "2"}),
            ContentBlock(type="tool_use", tool_use_id="c",
                         tool_name="echo", tool_input={"text": "3"}),
        ],
        stop_reason="tool_use", input_tokens=1, output_tokens=1, raw={},
    )
    done = LLMResponse(
        content=[ContentBlock(type="text", text="done")],
        stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={},
    )
    provider = FakeProvider([multi_tool, done])
    sess = _build_session(provider, tools=[EchoTool()], max_tool_calls_per_turn=2)
    outcome = sess.run_turn("multiple tools")
    assert outcome.n_tool_calls == 2  # third was capped


# ── Hooks ──────────────────────────────────────────────────────────────────


def test_on_turn_start_can_rewrite_user_input():
    captured: dict = {}
    class Rewriter:
        name = "rewriter"
        def on_turn_start(self, ctx, user_input):
            return UserInput(text=f"[wrapped] {user_input.text}")
        def before_llm_call(self, ctx, req):
            captured["messages"] = req.messages
            return None

    provider = FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="ok")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
    ])
    sess = _build_session(provider, plugins=[(Rewriter(),
        {"on_turn_start": 10, "before_llm_call": 10})])
    sess.run_turn("hello")
    assert captured["messages"][0].content == "[wrapped] hello"


def test_before_tool_call_can_deny():
    provider = FakeProvider([
        LLMResponse(
            content=[ContentBlock(type="tool_use", tool_use_id="x",
                                  tool_name="echo", tool_input={"text": "blocked"})],
            stop_reason="tool_use", input_tokens=1, output_tokens=1, raw={},
        ),
        LLMResponse(
            content=[ContentBlock(type="text", text="acknowledged")],
            stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={},
        ),
    ])
    class Denier:
        name = "denier"
        def before_tool_call(self, ctx, call):
            return ToolDenial(tool_call_id=call.tool_call_id, name=call.name,
                              reason="policy violation")

    recorder = EventRecorder()
    sess = _build_session(provider, tools=[EchoTool()],
                          plugins=[
                              (Denier(), {"before_tool_call": 10}),
                              (recorder, {"on_event": 100}),
                          ])
    outcome = sess.run_turn("trigger deny")
    assert outcome.success
    types = [e.type for e in recorder.events]
    assert EventType.TOOL_CALL_DENIED in types
    assert EventType.TOOL_CALL_STARTED not in types  # short-circuited


def test_after_llm_call_can_transform_response():
    """Plugin can replace the response — e.g., a "force structured output" plugin."""
    class Forcer:
        name = "forcer"
        def after_llm_call(self, ctx, req, resp):
            return LLMResponse(
                content=[ContentBlock(type="text", text="FORCED")],
                stop_reason="end_turn",
                input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                raw=resp.raw,
            )

    provider = FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="original")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
    ])
    sess = _build_session(provider, plugins=[(Forcer(), {"after_llm_call": 10})])
    outcome = sess.run_turn("anything")
    assert outcome.final_response == "FORCED"


# ── Session lifecycle ─────────────────────────────────────────────────────


def test_start_is_idempotent():
    sess = _build_session(FakeProvider([]))
    ctx1 = sess.start()
    ctx2 = sess.start()
    assert ctx1 is ctx2


def test_end_is_safe_without_start():
    sess = _build_session(FakeProvider([]))
    sess.end()  # should not raise


def test_session_started_event_lists_tools():
    recorder = EventRecorder()
    sess = _build_session(FakeProvider([]), tools=[EchoTool()],
                          plugins=[(recorder, {"on_event": 100})])
    sess.start()
    sess.end()
    started = next(e for e in recorder.events if e.type == EventType.SESSION_STARTED)
    assert started.payload["tools"] == ["echo"]
    assert started.payload["provider"] == "fake"


# ── Conversation persistence across turns ────────────────────────────────


def test_conversation_persists_across_turns():
    """Each turn appends to the same message list."""
    provider = FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="first")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
        LLMResponse(content=[ContentBlock(type="text", text="second")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
    ])
    sess = _build_session(provider)
    sess.run_turn("turn 1")
    sess.run_turn("turn 2")
    # First call's request has 1 user msg; second has 1 user + 1 assistant + 1 user = 3
    assert len(provider.calls[0].messages) == 1
    assert len(provider.calls[1].messages) == 3
