"""Tests for the guard plugin + UserGate abstractions."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from arc.plugins.guard import GuardPlugin
from arc.runtime.hooks import ToolCall, ToolDenial
from arc.user_gate import EscalationRequest, NoOpGate, UserGate


# ── Fake gates for testing ──────────────────────────────────────────────────


class _AlwaysAllowGate:
    def __init__(self):
        self.calls: list[EscalationRequest] = []
    def prompt_for_escalation(self, request):
        self.calls.append(request)
        return True


class _AlwaysDenyGate:
    def __init__(self):
        self.calls: list[EscalationRequest] = []
    def prompt_for_escalation(self, request):
        self.calls.append(request)
        return False


def _guard(*, allow=(), block=(), escalate=(), delegate=None, gate=None) -> GuardPlugin:
    return GuardPlugin(
        allowlist_tools=list(allow),
        blocklist_patterns=list(block),
        escalation_required_patterns=list(escalate),
        delegate_only_tools=dict(delegate or {}),
        user_gate=gate or NoOpGate(verbose=False),
    )


def _call(name="bash_exec", **input_kwargs) -> ToolCall:
    return ToolCall(tool_call_id="tcl_test", name=name, input=dict(input_kwargs))


# ── Allowlist ───────────────────────────────────────────────────────────────


def test_allowlisted_tool_bypasses_all_checks():
    g = _guard(allow=["ls"], block=["anything"], escalate=["everything"])
    # Even with input that would match, allowlisted tools pass
    result = g.before_tool_call(None, _call(name="ls", command="rm -rf /"))
    assert result is None


def test_tools_not_in_allowlist_get_checked():
    g = _guard(allow=["ls"], block=[r"rm\s+-rf"])
    result = g.before_tool_call(None, _call(command="rm -rf /tmp"))
    assert isinstance(result, ToolDenial)


# ── Delegate-only tools ─────────────────────────────────────────────────────


class _Ev:
    """Minimal session.started stand-in carrying the final tool list."""
    def __init__(self, tools):
        self.type = "session.started"
        self.payload = {"tools": list(tools)}


def _seen(g, *tools):
    """Feed the guard the tool list it would learn from session.started."""
    g.on_event(None, _Ev(tools))
    return g


def test_delegate_only_denies_parent_call_with_owner_hint():
    g = _seen(_guard(delegate={"container_*": "subagent_container_expert"}),
              "container_run", "subagent_container_expert")
    result = g.before_tool_call(None, _call(name="container_run"))
    assert isinstance(result, ToolDenial)
    assert "subagent_container_expert" in result.reason


def test_delegate_only_glob_matches_double_prefixed_name():
    # Works whether the MCP tool is registered as container_run or
    # container_container_run (unset vs empty tool_prefix).
    g = _seen(_guard(delegate={"container_*": "subagent_container_expert"}),
              "container_container_run", "subagent_container_expert")
    assert isinstance(
        g.before_tool_call(None, _call(name="container_container_run")), ToolDenial)


def test_delegate_only_allows_inside_subagent():
    from arc.runtime.subagents.tripwire import subagent_scope

    g = _seen(_guard(delegate={"container_*": "subagent_container_expert"}),
              "container_run", "subagent_container_expert")
    with subagent_scope():
        assert g.before_tool_call(None, _call(name="container_run")) is None


def test_delegate_only_does_not_block_the_dispatch_tool():
    # The main agent must still be able to CALL the sub-agent.
    g = _seen(_guard(delegate={"container_*": "subagent_container_expert"}),
              "container_run", "subagent_container_expert")
    assert g.before_tool_call(None, _call(name="subagent_container_expert")) is None


def test_delegate_only_ignores_unrelated_tools():
    g = _seen(_guard(delegate={"container_*": "subagent_container_expert"}),
              "container_run", "subagent_container_expert", "ghidra_decompile")
    assert g.before_tool_call(None, _call(name="ghidra_decompile")) is None


def test_delegate_only_allowlist_wins():
    # An allowlisted tool bypasses the delegate rule too.
    g = _seen(_guard(allow=["container_run"],
                     delegate={"container_*": "subagent_container_expert"}),
              "container_run", "subagent_container_expert")
    assert g.before_tool_call(None, _call(name="container_run")) is None


def test_delegate_only_fails_open_when_owner_absent():
    # Sub-agent disabled/uninstalled: the owner tool isn't in the registry,
    # so the rule must NOT brick container_run — it passes through.
    g = _seen(_guard(delegate={"container_*": "subagent_container_expert"}),
              "container_run")  # note: owner NOT present
    assert g.before_tool_call(None, _call(name="container_run")) is None


def test_delegate_only_fails_open_before_tools_are_known():
    # Before session.started is observed, we don't know the tool set, so we
    # don't enforce (never brick on a misconfigured/missing on_event wiring).
    g = _guard(delegate={"container_*": "subagent_container_expert"})
    assert g.before_tool_call(None, _call(name="container_run")) is None


# ── Blocklist ──────────────────────────────────────────────────────────────


def test_blocklist_denial_carries_useful_reason():
    g = _guard(block=[r"rm\s+-rf"])
    result = g.before_tool_call(None, _call(command="rm -rf /home"))
    assert isinstance(result, ToolDenial)
    assert "blocked pattern" in result.reason
    assert "rm" in result.reason


def test_blocklist_returns_denial_with_call_id_and_name():
    g = _guard(block=[r"rm\s+-rf"])
    result = g.before_tool_call(None, _call(command="rm -rf /home"))
    assert isinstance(result, ToolDenial)
    assert result.tool_call_id == "tcl_test"
    assert result.name == "bash_exec"


def test_blocklist_pattern_doesnt_match_innocent_command():
    g = _guard(block=[r"rm\s+-rf"])
    result = g.before_tool_call(None, _call(command="ls -la /tmp"))
    assert result is None


def test_multiple_blocklist_patterns_first_match_wins():
    g = _guard(block=[r"first", r"second"])
    result = g.before_tool_call(None, _call(command="something with first and second"))
    assert isinstance(result, ToolDenial)
    assert "first" in result.reason


def test_blocklist_handles_fork_bomb_pattern():
    g = _guard(block=[r":\(\)\s*\{"])
    result = g.before_tool_call(None, _call(command=":(){ :|: & };:"))
    assert isinstance(result, ToolDenial)


# ── Escalation ─────────────────────────────────────────────────────────────


def test_escalation_pattern_consults_gate_and_passes_through_on_approve():
    gate = _AlwaysAllowGate()
    g = _guard(escalate=[r"\bcurl\b"], gate=gate)
    result = g.before_tool_call(None, _call(command="curl https://api.example.com"))
    assert result is None  # passed through
    assert len(gate.calls) == 1
    assert "curl" in gate.calls[0].command


def test_escalation_pattern_denies_when_gate_denies():
    gate = _AlwaysDenyGate()
    g = _guard(escalate=[r"\bcurl\b"], gate=gate)
    result = g.before_tool_call(None, _call(command="curl https://api.example.com"))
    assert isinstance(result, ToolDenial)
    assert "escalation denied" in result.reason


def test_noop_gate_denies_escalations():
    g = _guard(escalate=[r"\bcurl\b"], gate=NoOpGate(verbose=False))
    result = g.before_tool_call(None, _call(command="curl https://example.com"))
    assert isinstance(result, ToolDenial)


def test_escalation_request_carries_full_context():
    captured = []
    class _CapturingGate:
        def prompt_for_escalation(self, request):
            captured.append(request)
            return True

    g = _guard(escalate=[r"\bwget\b"], gate=_CapturingGate())
    g.before_tool_call(None, _call(command="wget https://x.com/file"))
    assert captured[0].tool_name == "bash_exec"
    assert "wget" in captured[0].command
    assert "matches an escalation pattern" in captured[0].reason


# ── Blocklist beats escalation ────────────────────────────────────────────


def test_blocklist_checked_before_escalation():
    """If a command matches BOTH blocklist and escalation, blocklist wins."""
    gate = _AlwaysAllowGate()
    g = _guard(block=[r"\brm\b"], escalate=[r"\brm\b"], gate=gate)
    result = g.before_tool_call(None, _call(command="rm important.txt"))
    assert isinstance(result, ToolDenial)
    assert "blocked pattern" in result.reason
    # Gate should NOT have been called — blocklist short-circuited
    assert gate.calls == []


# ── Non-command inputs ─────────────────────────────────────────────────────


def test_non_command_tools_with_dangerous_strings_in_other_fields_pass():
    """Patterns only check `command`, not arbitrary fields. Otherwise we'd
    deny e.g. an ls request for a path that happens to contain 'curl'."""
    g = _guard(block=[r"\bcurl\b"], escalate=[r"\bwget\b"])
    result = g.before_tool_call(None, _call(name="ls", path="/var/log/curl-stuff"))
    assert result is None


def test_call_without_command_passes_when_not_allowlisted():
    g = _guard(allow=["ls"])
    # Some hypothetical other tool without a command field
    result = g.before_tool_call(None, _call(name="other", arg="x"))
    assert result is None
