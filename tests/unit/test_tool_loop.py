"""Unit tests for ToolLoop: termination, authorization, repeat detection, max_tokens."""
import pytest
from dataclasses import dataclass, field, asdict as _dc_asdict
from runtime.tool_loop import ToolLoop, ToolLoopConfig, ToolLoopResult
from runtime.guard import ActionGuard
from runtime.tool_executor import ToolCallExecutor
from runtime.tool_result import ToolResult
from runtime.identity import RuntimeIdentity


# ── Minimal fakes ─────────────────────────────────────────────────────────────

class _FakeMessenger:
    def __init__(self):
        self.messages = []
    def get_messages(self): return self.messages
    def add_user_message(self, m): self.messages.append({"role": "user", "content": m})
    def add_assistant_message(self, content):
        import dataclasses
        serialised = []
        for b in content:
            if dataclasses.is_dataclass(b) and not isinstance(b, type):
                serialised.append(dataclasses.asdict(b))
            elif hasattr(b, "__dict__"):
                serialised.append(vars(b))
            else:
                serialised.append(b)
        self.messages.append({"role": "assistant", "content": serialised})
    def add_tool_results(self, results):
        self.messages.append({"role": "user", "content": results})


class _FakeContextMgr:
    def pack(self, messages, query, **kwargs): return messages


class _FakeSpinner:
    def update(self, msg): pass
    def stop(self): pass
    def start(self, msg=None): pass


class _AutoDenyGate:
    def prompt(self, esc): return False


class _FakeRegistry:
    def __init__(self, tools=None):
        self._tools = tools or {}
    def get(self, name):
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name]


class _StrTool:
    def __init__(self, result): self._result = result
    def safe_execute(self, inp): return self._result


@dataclass
class _ProviderResponse:
    stop_reason: str
    content: list = field(default_factory=list)
    usage: object = None


from providers.base import TextBlock as _TextBlock, ToolUseBlock as _ToolUseBlock


class _ScriptedProvider:
    """Returns canned responses in sequence."""
    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    def _chat_impl(self, **kwargs):
        if self._idx >= len(self._responses):
            return _ProviderResponse(stop_reason="end_turn", content=[_TextBlock("done")])
        resp = self._responses[self._idx]
        self._idx += 1
        return resp

    def chat(self, **kwargs):
        return self._chat_impl(**kwargs)


def _make_loop(provider, tools=None, authorized=None, registry=None, max_tool_calls=15):
    registry = registry or _FakeRegistry(tools or {})
    guard = ActionGuard()
    executor = ToolCallExecutor(registry, guard, _AutoDenyGate())
    cfg = ToolLoopConfig(
        max_iterations=20,
        max_tool_calls=max_tool_calls,
        max_consecutive_errors=3,
        authorized_tool_names=frozenset(authorized or []),
        label="test",
    )
    return ToolLoop(
        provider=provider,
        messenger=_FakeMessenger(),
        context_mgr=_FakeContextMgr(),
        tool_executor=executor,
        user_gate=_AutoDenyGate(),
        config=cfg,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_end_turn_returns_text():
    provider = _ScriptedProvider([
        _ProviderResponse("end_turn", [_TextBlock("hello world")])
    ])
    loop = _make_loop(provider)
    result = loop.run(system="sys", tools=[], query="q")
    assert result.response_text == "hello world"
    assert not result.hit_max_tokens


def test_tool_call_then_end_turn():
    tool_block = _ToolUseBlock("id1", "echo_tool", {"msg": "hi"})
    provider = _ScriptedProvider([
        _ProviderResponse("tool_use", [tool_block]),
        _ProviderResponse("end_turn", [_TextBlock("done")]),
    ])
    registry = _FakeRegistry({"echo_tool": _StrTool("ECHO: hi")})
    loop = _make_loop(provider, registry=registry)
    result = loop.run(system="sys", tools=[{"name": "echo_tool"}], query="q")
    assert result.response_text == "done"


def test_unauthorized_tool_gets_rejection():
    tool_block = _ToolUseBlock("id1", "forbidden_tool", {})
    provider = _ScriptedProvider([
        _ProviderResponse("tool_use", [tool_block]),
        _ProviderResponse("end_turn", [_TextBlock("ok")]),
    ])
    loop = _make_loop(provider, authorized=["allowed_tool"])
    result = loop.run(system="sys", tools=[{"name": "allowed_tool"}], query="q")
    assert any("unauthorized" in e for e in result.tool_errors)


def test_repeat_identical_tool_call_triggers_force_end():
    tool_block = _ToolUseBlock("id1", "echo_tool", {"msg": "same"})
    provider = _ScriptedProvider([
        _ProviderResponse("tool_use", [tool_block]),
        _ProviderResponse("tool_use", [tool_block]),  # identical repeat
        _ProviderResponse("end_turn", [_TextBlock("done")]),
    ])
    registry = _FakeRegistry({"echo_tool": _StrTool("output")})
    loop = _make_loop(provider, registry=registry)
    result = loop.run(system="sys", tools=[{"name": "echo_tool"}], query="q")
    # Should have terminated without infinite loop
    assert result.response_text == "done"


def test_max_tokens_patches_dangling_tool_use():
    tool_block = _ToolUseBlock("id_dangling", "echo_tool", {})
    provider = _ScriptedProvider([
        _ProviderResponse("max_tokens", [tool_block]),
    ])
    registry = _FakeRegistry({"echo_tool": _StrTool("never called")})
    loop = _make_loop(provider, registry=registry)
    result = loop.run(system="sys", tools=[{"name": "echo_tool"}], query="q")
    assert result.hit_max_tokens
    # The messenger should have a synthetic tool_result for the dangling block
    msgs = loop._messenger.get_messages()
    tool_results = [m for m in msgs if isinstance(m.get("content"), list)
                    and any(b.get("type") == "tool_result" for b in m["content"])]
    assert len(tool_results) >= 1


def test_tool_call_cap_triggers_wrap_up():
    tool_block = _ToolUseBlock("id1", "echo_tool", {"msg": "hi"})
    # Script many tool responses; loop should cap at max_tool_calls=2
    responses = [_ProviderResponse("tool_use", [tool_block])] * 5
    responses.append(_ProviderResponse("end_turn", [_TextBlock("done")]))
    provider = _ScriptedProvider(responses)
    registry = _FakeRegistry({"echo_tool": _StrTool("result")})
    loop = _make_loop(provider, registry=registry, max_tool_calls=2)
    result = loop.run(system="sys", tools=[{"name": "echo_tool"}], query="q")
    assert result.hit_tool_call_cap
