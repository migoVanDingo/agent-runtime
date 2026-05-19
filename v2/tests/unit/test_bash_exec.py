"""Tests for the bash_exec tool."""
from __future__ import annotations

from pathlib import Path

import pytest

from arc.tools.base import ToolError
from arc.tools.bash_exec import BashExecTool


def _tool(**overrides) -> BashExecTool:
    base = dict(timeout_seconds=5, max_output_chars=10000, working_directory=None)
    base.update(overrides)
    return BashExecTool(**base)


# ── from_config ─────────────────────────────────────────────────────────────


def test_from_config_happy_path():
    t = BashExecTool.from_config({
        "timeout_seconds": 30,
        "max_output_chars": 50000,
        "working_directory": "/tmp",
    })
    assert t._default_timeout == 30
    assert t._max_chars == 50000
    assert t._default_cwd == "/tmp"


def test_from_config_missing_keys_raise():
    with pytest.raises(ValueError, match="missing required key.*timeout_seconds"):
        BashExecTool.from_config({"max_output_chars": 100})


# ── execute — happy path ────────────────────────────────────────────────────


def test_execute_simple_echo():
    t = _tool()
    out = t.execute({"command": "echo hello"})
    assert out.strip() == "hello"


def test_execute_with_stderr():
    t = _tool()
    out = t.execute({"command": "echo on-stdout && echo on-stderr 1>&2"})
    assert "on-stdout" in out
    assert "STDERR:" in out
    assert "on-stderr" in out


def test_execute_no_output_clean_exit_marks_it():
    t = _tool()
    out = t.execute({"command": "true"})
    assert "no output" in out
    assert "exit code 0" in out


def test_execute_non_zero_exit_prefixes_error():
    t = _tool()
    out = t.execute({"command": "false"})
    assert out.startswith("Error: exit code 1")


def test_execute_non_zero_exit_no_output_message():
    t = _tool()
    out = t.execute({"command": "exit 7"})
    assert "exit code 7" in out


# ── cwd handling ────────────────────────────────────────────────────────────


def test_execute_respects_per_call_cwd(tmp_path):
    t = _tool()
    out = t.execute({"command": "pwd", "cwd": str(tmp_path)})
    assert out.strip() == str(tmp_path.resolve())


def test_execute_uses_default_cwd_when_configured(tmp_path):
    t = _tool(working_directory=str(tmp_path))
    out = t.execute({"command": "pwd"})
    assert out.strip() == str(tmp_path.resolve())


def test_execute_nonexistent_cwd_raises_tool_error(tmp_path):
    t = _tool()
    with pytest.raises(ToolError, match="could not run command"):
        t.execute({"command": "pwd", "cwd": str(tmp_path / "does/not/exist")})


# ── timeout ─────────────────────────────────────────────────────────────────


def test_execute_timeout_returns_marker():
    t = _tool(timeout_seconds=1)
    out = t.execute({"command": "sleep 5"})
    assert "Error: command timed out" in out
    assert "1s" in out


def test_execute_per_call_timeout_overrides_default():
    t = _tool(timeout_seconds=60)
    out = t.execute({"command": "sleep 5", "timeout_seconds": 1})
    assert "timed out after 1s" in out


# ── truncation ──────────────────────────────────────────────────────────────


def test_execute_truncates_long_output():
    t = _tool(max_output_chars=100)
    out = t.execute({"command": "yes hello | head -c 1000"})
    assert "[truncated; original was" in out
    # The truncated body + trailer should be near the limit, not the raw 1000
    assert len(out) < 300


# ── input validation ────────────────────────────────────────────────────────


def test_execute_empty_command_raises():
    t = _tool()
    with pytest.raises(ToolError, match="non-empty 'command'"):
        t.execute({"command": ""})


def test_execute_missing_command_raises():
    t = _tool()
    with pytest.raises(ToolError, match="non-empty 'command'"):
        t.execute({})


def test_execute_non_string_command_raises():
    t = _tool()
    with pytest.raises(ToolError, match="non-empty 'command'"):
        t.execute({"command": ["ls"]})


# ── filesystem side-effects (verifying bash_exec works for real workflows) ─


def test_execute_can_create_file(tmp_path):
    t = _tool()
    target = tmp_path / "out.txt"
    out = t.execute({"command": f"echo line1 > {target}"})
    assert target.read_text().strip() == "line1"


def test_execute_heredoc_works(tmp_path):
    t = _tool()
    target = tmp_path / "poem.txt"
    cmd = f"cat > {target} << 'EOF'\nfirst line\nsecond line\nEOF"
    t.execute({"command": cmd})
    assert target.read_text() == "first line\nsecond line\n"


# ── input schema ────────────────────────────────────────────────────────────


def test_input_schema_includes_command_and_optional_cwd_timeout():
    t = _tool()
    schema = t.input_schema
    assert "command" in schema.properties
    assert "cwd" in schema.properties
    assert "timeout_seconds" in schema.properties
    assert schema.required == ["command"]


def test_input_schema_description_includes_default_timeout():
    t = _tool(timeout_seconds=42)
    schema = t.input_schema
    assert "42" in schema.properties["timeout_seconds"]["description"]
