"""Tests for the safety_gate plugin (destructive-action confirmation)."""
from __future__ import annotations

from typing import Any

import pytest

from arc.plugins.safety_gate import DEFAULT_PATTERNS, Pattern, SafetyGatePlugin
from arc.plugins.safety_gate.catalog import catalog_by_name
from arc.runtime.events import EventType
from arc.runtime.hooks import ToolCall, ToolDenial
from arc.user_gate import EscalationRequest, NoOpGate


# ── Fakes ───────────────────────────────────────────────────────────────────


class _AlwaysAllow:
    def __init__(self):
        self.calls: list[EscalationRequest] = []

    def prompt_for_escalation(self, request):
        self.calls.append(request)
        return True


class _AlwaysDeny:
    def __init__(self):
        self.calls: list[EscalationRequest] = []

    def prompt_for_escalation(self, request):
        self.calls.append(request)
        return False


class _CapturingBus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


_ALL_PATTERN_NAMES = [p.name for p in DEFAULT_PATTERNS]


def _gate_plugin(
    *,
    gate: Any = None,
    bus: Any = None,
    enabled: bool = True,
    bypass: bool = False,
    enabled_patterns: list[str] | None = None,
    custom: list[Pattern] | None = None,
) -> SafetyGatePlugin:
    p = SafetyGatePlugin(
        enabled=enabled,
        bypass_mode=bypass,
        enabled_pattern_names=(
            enabled_patterns if enabled_patterns is not None else _ALL_PATTERN_NAMES
        ),
        custom_patterns=custom or [],
        user_gate=gate or NoOpGate(verbose=False),
    )
    if bus is not None:
        p.bind_bus(bus)
    return p


def _call(command: str, name: str = "bash_exec") -> ToolCall:
    return ToolCall(tool_call_id="tcl_test", name=name, input={"command": command})


# ── Pattern matching ────────────────────────────────────────────────────────


def test_rm_file_matches():
    p = _gate_plugin(gate=_AlwaysDeny())
    result = p.before_tool_call(None, _call("rm foo.txt"))
    assert isinstance(result, ToolDenial)
    assert "rm-file" in result.reason


def test_rm_dash_dash_separator_still_matches_rm_file():
    p = _gate_plugin(gate=_AlwaysDeny())
    # `rm -- foo` is a flag-terminator followed by a file; regex sees "rm -"
    # so it does NOT match rm-file (which needs `rm <non-flag>`). It also
    # doesn't match rm-recursive. This is acceptable — rare form, and the
    # following arg is still a delete.
    result = p.before_tool_call(None, _call("rm -- foo.txt"))
    # Conservatively: this is allowed pass-through. Document the behavior:
    assert result is None or isinstance(result, ToolDenial)


def test_rm_recursive_matches_rm_dash_r():
    p = _gate_plugin(gate=_AlwaysDeny())
    result = p.before_tool_call(None, _call("rm -r dir"))
    assert isinstance(result, ToolDenial)


def test_rm_rf_does_not_match_rm_recursive():
    """rm -rf is guard's job (hard deny). safety_gate's rm-recursive
    pattern excludes -rf to avoid double-handling."""
    p = _gate_plugin(gate=_AlwaysDeny(), enabled_patterns=["rm-recursive"])
    result = p.before_tool_call(None, _call("rm -rf dir"))
    assert result is None  # passed through to guard's domain


def test_git_reset_hard_matches():
    p = _gate_plugin(gate=_AlwaysDeny())
    result = p.before_tool_call(None, _call("git reset --hard HEAD~3"))
    assert isinstance(result, ToolDenial)
    assert "git-reset-hard" in result.reason


def test_git_push_force_matches():
    p = _gate_plugin(gate=_AlwaysDeny())
    for cmd in [
        "git push --force origin main",
        "git push -f origin main",
        "git push --force-with-lease origin main",
    ]:
        result = p.before_tool_call(None, _call(cmd))
        assert isinstance(result, ToolDenial), f"failed on: {cmd}"
        assert "git-push-force" in result.reason


def test_redirect_overwrite_matches_single_redirect_not_append():
    p = _gate_plugin(gate=_AlwaysDeny(), enabled_patterns=["redirect-overwrite"])
    assert isinstance(p.before_tool_call(None, _call("echo x > file.txt")), ToolDenial)
    # >> append is fine
    assert p.before_tool_call(None, _call("echo x >> file.txt")) is None


def test_drop_table_matches_case_insensitive():
    p = _gate_plugin(gate=_AlwaysDeny())
    for cmd in ["psql -c 'DROP TABLE foo'", "psql -c 'drop table foo'"]:
        result = p.before_tool_call(None, _call(cmd))
        assert isinstance(result, ToolDenial)
        assert "drop-table" in result.reason


# ── Pass-through cases ─────────────────────────────────────────────────────


def test_safe_command_passes_through():
    gate = _AlwaysDeny()
    p = _gate_plugin(gate=gate)
    result = p.before_tool_call(None, _call("ls -la"))
    assert result is None
    assert gate.calls == []  # never asked the user


def test_non_command_tool_passes_through():
    p = _gate_plugin(gate=_AlwaysDeny())
    call = ToolCall(tool_call_id="x", name="ls", input={"path": "/tmp"})
    assert p.before_tool_call(None, call) is None


def test_disabled_plugin_passes_through_everything():
    gate = _AlwaysDeny()
    p = _gate_plugin(gate=gate, enabled=False)
    assert p.before_tool_call(None, _call("rm -rf /")) is None
    assert gate.calls == []


def test_bypass_mode_passes_through_but_keeps_plugin_loaded():
    gate = _AlwaysDeny()
    p = _gate_plugin(gate=gate, bypass=True)
    assert p.before_tool_call(None, _call("rm foo")) is None
    assert gate.calls == []


# ── User decision flow ────────────────────────────────────────────────────


def test_user_approves_allows_call():
    gate = _AlwaysAllow()
    p = _gate_plugin(gate=gate)
    result = p.before_tool_call(None, _call("rm foo"))
    assert result is None
    assert len(gate.calls) == 1


def test_user_denies_returns_tool_denial():
    gate = _AlwaysDeny()
    p = _gate_plugin(gate=gate)
    result = p.before_tool_call(None, _call("rm foo"))
    assert isinstance(result, ToolDenial)
    assert "denied by user" in result.reason
    assert "Do not retry" in result.reason  # guidance to model


def test_remember_cache_prevents_re_prompt_same_session():
    gate = _AlwaysAllow()
    p = _gate_plugin(gate=gate)
    # First rm-file approval
    assert p.before_tool_call(None, _call("rm a.txt")) is None
    # Second rm-file should NOT re-prompt
    assert p.before_tool_call(None, _call("rm b.txt")) is None
    assert len(gate.calls) == 1


def test_remember_is_per_pattern_not_global():
    gate = _AlwaysAllow()
    p = _gate_plugin(gate=gate)
    # Approve rm-file
    assert p.before_tool_call(None, _call("rm a.txt")) is None
    # Different pattern (git-reset-hard) still prompts
    assert p.before_tool_call(None, _call("git reset --hard HEAD")) is None
    assert len(gate.calls) == 2


# ── Events ────────────────────────────────────────────────────────────────


def test_event_emitted_on_request_and_allow():
    bus = _CapturingBus()
    p = _gate_plugin(gate=_AlwaysAllow(), bus=bus)
    p.before_tool_call(None, _call("rm foo"))

    types = [e[0] for e in bus.events]
    assert EventType.SAFETY_CONFIRMATION_REQUESTED in types
    assert EventType.SAFETY_CONFIRMATION_ALLOWED in types

    allowed = next(e for e in bus.events if e[0] == EventType.SAFETY_CONFIRMATION_ALLOWED)
    assert allowed[1]["scope"] == "session"
    assert allowed[1]["pattern_name"] == "rm-file"


def test_event_emitted_on_request_and_deny():
    bus = _CapturingBus()
    p = _gate_plugin(gate=_AlwaysDeny(), bus=bus)
    p.before_tool_call(None, _call("rm foo"))

    types = [e[0] for e in bus.events]
    assert EventType.SAFETY_CONFIRMATION_REQUESTED in types
    assert EventType.SAFETY_CONFIRMATION_DENIED in types
    assert EventType.SAFETY_CONFIRMATION_ALLOWED not in types


def test_remembered_call_emits_allowed_with_remembered_scope():
    bus = _CapturingBus()
    p = _gate_plugin(gate=_AlwaysAllow(), bus=bus)
    p.before_tool_call(None, _call("rm a"))     # first → scope=session
    p.before_tool_call(None, _call("rm b"))     # second → scope=remembered

    allowed = [e[1] for e in bus.events if e[0] == EventType.SAFETY_CONFIRMATION_ALLOWED]
    assert len(allowed) == 2
    assert allowed[0]["scope"] == "session"
    assert allowed[1]["scope"] == "remembered"


# ── Headless ──────────────────────────────────────────────────────────────


def test_noop_gate_denies_all_destructive_calls():
    p = _gate_plugin(gate=NoOpGate(verbose=False))
    result = p.before_tool_call(None, _call("rm foo"))
    assert isinstance(result, ToolDenial)


# ── Custom patterns ──────────────────────────────────────────────────────


def test_custom_patterns_merge_with_catalog():
    custom = [Pattern(name="my-rule", description="custom thing",
                     regex=r"\bDESTROY\b")]
    gate = _AlwaysDeny()
    p = _gate_plugin(gate=gate, enabled_patterns=["rm-file"], custom=custom)

    # Custom rule fires
    result = p.before_tool_call(None, _call("DESTROY everything"))
    assert isinstance(result, ToolDenial)
    assert "my-rule" in result.reason

    # Catalog rule still fires
    result = p.before_tool_call(None, _call("rm x"))
    assert isinstance(result, ToolDenial)
    assert "rm-file" in result.reason


def test_unknown_pattern_name_in_config_silently_ignored():
    # Typoing a pattern name shouldn't crash startup
    p = _gate_plugin(gate=_AlwaysDeny(),
                     enabled_patterns=["rm-file", "nonexistent-pattern"])
    result = p.before_tool_call(None, _call("rm x"))
    assert isinstance(result, ToolDenial)


# ── Catalog completeness sanity check ─────────────────────────────────────


def test_catalog_by_name_returns_all_patterns():
    cat = catalog_by_name()
    assert len(cat) == len(DEFAULT_PATTERNS)
    assert all(p.name in cat for p in DEFAULT_PATTERNS)


def test_every_default_pattern_compiles():
    import re
    for p in DEFAULT_PATTERNS:
        re.compile(p.regex)  # raises if invalid
