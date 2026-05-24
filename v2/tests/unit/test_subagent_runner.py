"""SubAgentRunner end-to-end dispatch with a mocked provider.

Goals:
  - dispatch() returns ok with a real (mocked) child AgentSession
  - parent's bus sees subagent.dispatched + subagent.returned
  - child's bus is separate (those events do NOT leak to parent)
  - timeout produces status=timeout
  - missing-tool spec is a hard error at dispatch
  - expected_output is appended to the child's system prompt
"""
from __future__ import annotations

from pathlib import Path

import pytest

from arc.config import (
    BootstrapConfig, Config, PluginsConfig, ProviderConfig,
    RetryConfig, RuntimeConfig, ToolsConfig, TUIConfig,
)
from arc.runtime.bus import EventBus, HookRegistry
from arc.runtime.events import EventType, RuntimeEvent
from arc.runtime.hooks import ContentBlock, LLMRequest, LLMResponse
from arc.runtime.subagents.registry import SubAgentRegistry
from arc.runtime.subagents.runner import SubAgentRunner
from arc.subagent_api import SubAgentSpec
from arc.tools.base import ToolRegistry


# ── Test infrastructure ───────────────────────────────────────────────────


def _parent_config() -> Config:
    return Config(
        runtime=RuntimeConfig(
            workspace=".",
            max_iterations=50, max_tool_calls_per_turn=30,
            show_thinking=False, log_level="info",
            system_prompt="parent prompt",
            iteration_cap_message="wrap up",
            tool_call_cap_message="wrap up",
            cycle_detection_threshold=3,
            cycle_detected_message="cycle",
        ),
        provider=ProviderConfig(
            name="fake", model="fake-1", api_key_env="X", base_url=None,
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
            show_token_counts=False, show_event_count=False,
            show_thinking=False, tool_output_max_lines=30,
            toolbar_enabled=False, input_history_enabled=False,
        ),
        bootstrap=BootstrapConfig(create_workspace_dir=False, write_example_session=False),
        source_path=Path("/tmp/fake.yml"),
    )


class FakeProvider:
    """Returns scripted responses. One response per chat() call."""
    name = "fake"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._queue = list(responses)
        self.calls: list[LLMRequest] = []

    def chat(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        if not self._queue:
            return LLMResponse(
                content=[ContentBlock(type="text", text="(default)")],
                stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={},
            )
        return self._queue.pop(0)


def _registry_with(specs: dict[str, SubAgentSpec]) -> SubAgentRegistry:
    reg = SubAgentRegistry(builtins=dict(specs), entry_point_loader=lambda: [])
    reg.discover({})
    return reg


def _capture_events(bus: EventBus, registry: HookRegistry) -> list[RuntimeEvent]:
    """Subscribe an observer that records every emitted event."""
    captured: list[RuntimeEvent] = []

    class _Capture:
        name = "_capture"
        def on_event(self, ctx, event):
            captured.append(event)

    registry.register(_Capture(), hooks_order={"on_event": 200})
    return captured


# ── Tests ─────────────────────────────────────────────────────────────────


def test_dispatch_ok(monkeypatch):
    """One-turn ok dispatch produces status=ok with the final assistant text."""
    spec = SubAgentSpec(
        name="echo", description="d", provider="anthropic",
        model="claude-haiku-4-5", system_prompt="say hello", tools=(),
        timeout_s=10.0, max_turns=2,
    )
    reg = _registry_with({"echo": spec})

    # Parent infrastructure.
    parent_reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    parent_bus = EventBus(parent_reg)
    captured = _capture_events(parent_bus, parent_reg)

    # Patch the provider builder so build_provider() returns our fake.
    fake = FakeProvider([
        LLMResponse(
            content=[ContentBlock(type="text", text='{"echo":"hi","length":2}')],
            stop_reason="end_turn", input_tokens=10, output_tokens=20, raw={},
        ),
    ])
    monkeypatch.setattr("arc.providers.build", lambda cfg: fake)

    runner = SubAgentRunner(
        registry=reg, parent_bus=parent_bus, parent_tools=ToolRegistry(),
        parent_config=_parent_config(), arc_home=Path("/tmp"), sessions_dir=Path("/tmp"),
    )
    result = runner.dispatch("echo", "say hi", parent_session_id="parent_1")

    assert result.status == "ok"
    assert result.output == '{"echo":"hi","length":2}'
    assert result.turns == 1
    assert result.tool_calls == 0
    # input + output tokens from the (single) child llm.call.completed
    types = [e.type for e in captured]
    assert EventType.SUBAGENT_DISPATCHED in types
    assert EventType.SUBAGENT_RETURNED in types


def test_parent_bus_does_not_see_child_internals(monkeypatch):
    """Child's llm.call.* and turn.* events stay on the child's bus."""
    spec = SubAgentSpec(
        name="echo", description="d", provider="anthropic",
        model="m", system_prompt="p", tools=(),
        timeout_s=10.0, max_turns=2,
    )
    reg = _registry_with({"echo": spec})
    parent_reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    parent_bus = EventBus(parent_reg)
    captured = _capture_events(parent_bus, parent_reg)

    fake = FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="ok")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
    ])
    monkeypatch.setattr("arc.providers.build", lambda cfg: fake)

    runner = SubAgentRunner(
        registry=reg, parent_bus=parent_bus, parent_tools=ToolRegistry(),
        parent_config=_parent_config(), arc_home=Path("/tmp"), sessions_dir=Path("/tmp"),
    )
    runner.dispatch("echo", "task", parent_session_id="parent_1")

    parent_types = [e.type for e in captured]
    # Child events MUST NOT appear on the parent's bus.
    for forbidden in (EventType.LLM_CALL_STARTED, EventType.LLM_CALL_COMPLETED,
                      EventType.TURN_STARTED, EventType.TURN_ENDED,
                      EventType.SESSION_STARTED, EventType.SESSION_ENDED):
        assert forbidden not in parent_types, (
            f"child event {forbidden!r} leaked to parent bus"
        )


def test_missing_tool_in_spec_raises(monkeypatch):
    """Spec declares a tool the parent doesn't have → hard error."""
    spec = SubAgentSpec(
        name="needy", description="d", provider="anthropic",
        model="m", system_prompt="p", tools=("nonexistent_tool",),
        timeout_s=10.0, max_turns=2,
    )
    reg = _registry_with({"needy": spec})
    parent_reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    parent_bus = EventBus(parent_reg)

    monkeypatch.setattr("arc.providers.build", lambda cfg: FakeProvider([]))

    runner = SubAgentRunner(
        registry=reg, parent_bus=parent_bus, parent_tools=ToolRegistry(),
        parent_config=_parent_config(), arc_home=Path("/tmp"), sessions_dir=Path("/tmp"),
    )
    from arc.subagent_api import SubAgentError
    with pytest.raises(SubAgentError, match="not available"):
        runner.dispatch("needy", "task", parent_session_id="parent_1")


def test_expected_output_appended_to_system_prompt(monkeypatch):
    """When spec.expected_output is set, the runner appends it to the child's prompt."""
    spec = SubAgentSpec(
        name="echo", description="d", provider="anthropic",
        model="m", system_prompt="base prompt", tools=(),
        expected_output='{"x": int}',
        timeout_s=10.0, max_turns=2,
    )
    reg = _registry_with({"echo": spec})
    parent_reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    parent_bus = EventBus(parent_reg)

    fake = FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="ok")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
    ])
    monkeypatch.setattr("arc.providers.build", lambda cfg: fake)

    runner = SubAgentRunner(
        registry=reg, parent_bus=parent_bus, parent_tools=ToolRegistry(),
        parent_config=_parent_config(), arc_home=Path("/tmp"), sessions_dir=Path("/tmp"),
    )
    runner.dispatch("echo", "task", parent_session_id="parent_1")

    assert len(fake.calls) >= 1
    sent_system = fake.calls[0].system
    assert "base prompt" in sent_system
    assert '{"x": int}' in sent_system


def test_spec_params_thread_into_child_provider_config(monkeypatch):
    """spec.params merges into the child's ProviderConfig.params at dispatch."""
    spec = SubAgentSpec(
        name="vertex_thing", description="d",
        provider="vertex_gemini", model="gemini-2.5-pro",
        system_prompt="p", tools=(),
        params={"vertex_project_id": "my-proj", "vertex_region": "us-east1"},
    )
    reg = _registry_with({"vertex_thing": spec})
    parent_reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    parent_bus = EventBus(parent_reg)

    seen_configs = []

    class FakeForCapture:
        name = "fake"
        def chat(self, req):
            return LLMResponse(
                content=[ContentBlock(type="text", text="ok")],
                stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={},
            )

    def _fake_build(cfg):
        seen_configs.append(cfg)
        return FakeForCapture()

    monkeypatch.setattr("arc.providers.build", _fake_build)

    runner = SubAgentRunner(
        registry=reg, parent_bus=parent_bus, parent_tools=ToolRegistry(),
        parent_config=_parent_config(), arc_home=Path("/tmp"), sessions_dir=Path("/tmp"),
    )
    runner.dispatch("vertex_thing", "task", parent_session_id="parent_1")

    assert len(seen_configs) == 1
    child_cfg = seen_configs[0]
    assert child_cfg.params["vertex_project_id"] == "my-proj"
    assert child_cfg.params["vertex_region"] == "us-east1"


def test_quota_denied_returns_error_without_running(monkeypatch):
    """Once quota is exhausted, dispatch returns error and does NOT call provider."""
    spec = SubAgentSpec(
        name="echo", description="d", provider="anthropic",
        model="m", system_prompt="p", tools=(),
        max_dispatches_per_session=1, timeout_s=10.0, max_turns=2,
    )
    reg = _registry_with({"echo": spec})
    parent_reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    parent_bus = EventBus(parent_reg)
    captured = _capture_events(parent_bus, parent_reg)

    fake = FakeProvider([
        LLMResponse(content=[ContentBlock(type="text", text="ok")],
                    stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={}),
    ])
    monkeypatch.setattr("arc.providers.build", lambda cfg: fake)

    runner = SubAgentRunner(
        registry=reg, parent_bus=parent_bus, parent_tools=ToolRegistry(),
        parent_config=_parent_config(), arc_home=Path("/tmp"), sessions_dir=Path("/tmp"),
    )
    # First call uses the only slot.
    r1 = runner.dispatch("echo", "task", parent_session_id="parent_1")
    assert r1.status == "ok"
    # Second call denied.
    r2 = runner.dispatch("echo", "task", parent_session_id="parent_1")
    assert r2.status == "error"
    assert "quota exceeded" in r2.error_message
    # Provider was only called once (for the allowed dispatch).
    assert len(fake.calls) == 1
    # And subagent.quota_exceeded fired.
    types = [e.type for e in captured]
    assert EventType.SUBAGENT_QUOTA_EXCEEDED in types
