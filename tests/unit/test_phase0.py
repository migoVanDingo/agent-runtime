"""Phase 0 unit tests — migrated from unittest to pytest."""
import pytest

from runtime.events import EventBus, RuntimeEvent
from runtime.guard import ActionGuard, GuardDecision
from runtime.identity import RuntimeIdentity
from runtime.json_extract import extract_json
from runtime.policy import PathPolicy
from runtime.sandbox import SandboxManager
from runtime.tool_result import ToolResult


class _CollectSink:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


# ── RuntimeIdentity ──────────────────────────────────────────────────

def test_identity_derives_scoped_ids_without_losing_session():
    base = RuntimeIdentity.new_session(session_id="SESS123", project_id="proj")
    turn = base.for_turn("TURN123").for_pipeline("RUN123").for_tool_call("TCALL123")
    assert turn.session_id == "SESS123"
    assert turn.turn_id == "TURN123"
    assert turn.pipeline_run_id == "RUN123"
    assert turn.tool_call_id == "TCALL123"
    assert turn.project_id == "proj"


def test_identity_for_plan_preserves_turn():
    base = RuntimeIdentity.new_session(session_id="S1")
    turn = base.for_turn()
    plan = turn.for_plan("PL1")
    assert plan.session_id == "S1"
    assert plan.plan_id == "PL1"
    assert plan.turn_id == turn.turn_id


# ── EventBus ─────────────────────────────────────────────────────────

def test_event_bus_emits_to_sink():
    sink = _CollectSink()
    bus = EventBus([sink])
    identity = RuntimeIdentity.new_session(session_id="SESS123")
    event = RuntimeEvent("unit.test", identity, payload={"ok": True}, stage="TestStage")
    bus.emit(event)
    assert len(sink.events) == 1
    data = sink.events[0].to_dict()
    assert data["event_type"] == "unit.test"
    assert data["session_id"] == "SESS123"
    assert data["payload"] == {"ok": True}


def test_noop_bus_drops_events():
    sink = _CollectSink()
    bus = EventBus([sink], enabled=False)
    bus.emit(RuntimeEvent("unit.test", RuntimeIdentity.new_session(session_id="S")))
    assert sink.events == []


def test_event_bus_fans_out_to_multiple_sinks():
    s1, s2 = _CollectSink(), _CollectSink()
    bus = EventBus([s1, s2])
    bus.emit(RuntimeEvent("x", RuntimeIdentity.new_session(session_id="S")))
    assert len(s1.events) == 1
    assert len(s2.events) == 1


# ── ToolResult ───────────────────────────────────────────────────────

def test_tool_result_success():
    r = ToolResult.success("hello")
    assert r.ok is True
    assert r.to_llm_content() == "hello"
    assert r.error_code is None


def test_tool_result_error():
    r = ToolResult.error("Error: failed", error_code="failed")
    assert r.ok is False
    assert r.to_llm_content() == "Error: failed"
    assert r.error_code == "failed"


# ── ActionGuard ──────────────────────────────────────────────────────

def test_shell_dangerous_command_blocks():
    decision, reason = ActionGuard().check_tool_call("bash_exec", {"command": "rm -rf /tmp/example"})
    assert decision == GuardDecision.BLOCK
    assert "dangerous command" in reason


def test_shell_network_command_escalates():
    decision, reason = ActionGuard().check_tool_call("bash_exec", {"command": "curl https://example.com"})
    assert decision == GuardDecision.ESCALATE
    assert "network command" in reason


def test_delete_file_always_escalates():
    decision, _ = ActionGuard().check_tool_call("delete_file", {"path": "/tmp/foo.txt"})
    assert decision == GuardDecision.ESCALATE


def test_write_file_sensitive_path_escalates():
    decision, _ = ActionGuard().check_tool_call("write_file", {"path": "/etc/passwd", "content": "x"})
    assert decision == GuardDecision.ESCALATE


def test_read_file_safe_path_allows():
    decision, _ = ActionGuard().check_tool_call("read_file", {"path": "README.md"})
    assert decision == GuardDecision.ALLOW


# ── SandboxManager ───────────────────────────────────────────────────

class _HostOnlyCfg:
    backend = "host"
    allow_host_backend = True
    docker_image = "python:3.11-slim"
    default_network = "disabled"
    command_timeout_seconds = 5
    max_output_chars = 1000
    workspace_root = "."
    allowed_read_roots = []
    allowed_write_roots = []


def test_host_sandbox_runs_command():
    result = SandboxManager(_HostOnlyCfg()).run_shell("printf hello")
    assert result.stdout == "hello"
    assert result.sandbox_backend == "host"
    assert result.isolation == "none"


def test_host_sandbox_captures_stderr():
    result = SandboxManager(_HostOnlyCfg()).run_shell("echo err >&2")
    assert "err" in result.stderr


# ── PathPolicy ───────────────────────────────────────────────────────

def test_path_policy_allows_workspace():
    policy = PathPolicy(workspace_root=".", allowed_read_roots=[], allowed_write_roots=[])
    assert policy.check("README.md", "read").allowed is True


def test_path_policy_blocks_external():
    policy = PathPolicy(workspace_root=".", allowed_read_roots=[], allowed_write_roots=[])
    decision = policy.check("/etc/passwd", "read")
    assert decision.allowed is False
    assert "outside allowed roots" in decision.reason


def test_path_policy_allows_configured_tmp():
    policy = PathPolicy(workspace_root=".", allowed_read_roots=[], allowed_write_roots=["/tmp"])
    assert policy.check("/tmp/agent-test.txt", "write").allowed is True


def test_path_policy_blocks_dotdot_escape():
    policy = PathPolicy(workspace_root="/project", allowed_read_roots=[], allowed_write_roots=[])
    decision = policy.check("/project/../etc/passwd", "read")
    assert decision.allowed is False


# ── extract_json ─────────────────────────────────────────────────────

def test_extracts_fenced_json():
    assert extract_json('```json\n{"ok": true}\n```') == {"ok": True}


def test_extracts_json_with_surrounding_text():
    assert extract_json('prefix {"a": 1, "b": [2]} suffix') == {"a": 1, "b": [2]}


def test_extracts_bare_json():
    assert extract_json('{"verdict": "approved"}') == {"verdict": "approved"}


def test_returns_none_on_garbage():
    assert extract_json("this is not json at all") is None


def test_extracts_array():
    assert extract_json("[1, 2, 3]") == [1, 2, 3]
