"""Tests for tool base + ls tool + tool factory."""
from __future__ import annotations

from pathlib import Path

import pytest

from arc.config import ToolsConfig
from arc.tools import build
from arc.tools.base import Tool, ToolError, ToolInputSchema, ToolRegistry
from arc.tools.ls import LSTool


# ── ToolInputSchema ────────────────────────────────────────────────────────


def test_input_schema_serializes_to_json_schema():
    s = ToolInputSchema(
        properties={"x": {"type": "string"}},
        required=["x"],
    )
    assert s.to_json_schema() == {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
    }


# ── ToolRegistry ───────────────────────────────────────────────────────────


def test_register_and_get_roundtrip():
    reg = ToolRegistry()
    t = LSTool(max_depth=2, show_hidden=False)
    reg.register(t)
    assert reg.get("ls") is t
    assert "ls" in reg
    assert reg.names() == ["ls"]
    assert len(reg) == 1


def test_get_unknown_raises():
    reg = ToolRegistry()
    with pytest.raises(KeyError, match="not registered"):
        reg.get("nope")


def test_double_register_raises():
    reg = ToolRegistry()
    reg.register(LSTool(max_depth=2, show_hidden=False))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(LSTool(max_depth=3, show_hidden=True))


# ── LSTool ─────────────────────────────────────────────────────────────────


def test_ls_flat_listing(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "c.txt").write_text("c")
    (tmp_path / "sub").mkdir()

    tool = LSTool(max_depth=2, show_hidden=False)
    result = tool.execute({"path": str(tmp_path)})
    lines = result.splitlines()
    assert "a.txt" in lines
    assert "b.txt" in lines
    assert "c.txt" in lines
    assert "sub/" in lines


def test_ls_sorts_alphabetically(tmp_path):
    for name in ["zeta", "alpha", "middle"]:
        (tmp_path / name).write_text("")
    tool = LSTool(max_depth=1, show_hidden=False)
    result = tool.execute({"path": str(tmp_path)})
    assert result.splitlines() == ["alpha", "middle", "zeta"]


def test_ls_hides_dotfiles_by_default(tmp_path):
    (tmp_path / "visible").write_text("")
    (tmp_path / ".hidden").write_text("")
    tool = LSTool(max_depth=1, show_hidden=False)
    result = tool.execute({"path": str(tmp_path)})
    assert "visible" in result
    assert ".hidden" not in result


def test_ls_shows_hidden_when_configured(tmp_path):
    (tmp_path / "visible").write_text("")
    (tmp_path / ".hidden").write_text("")
    tool = LSTool(max_depth=1, show_hidden=True)
    result = tool.execute({"path": str(tmp_path)})
    assert "visible" in result
    assert ".hidden" in result


def test_ls_recursive_respects_depth(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "file").write_text("")

    # depth=1: only top-level "a/"
    tool = LSTool(max_depth=5, show_hidden=False)
    result = tool.execute({"path": str(tmp_path), "depth": 1})
    assert "a/" in result
    assert "b" not in result.replace("a/", "")  # "a/" appears, but no "b"

    # depth=2: "a/", "a/b/"
    result2 = tool.execute({"path": str(tmp_path), "depth": 2})
    assert "a/b/" in result2
    assert "a/b/file" not in result2  # file is at depth 3

    # depth=3: all visible
    result3 = tool.execute({"path": str(tmp_path), "depth": 3})
    assert "a/b/file" in result3


def test_ls_depth_capped_at_max(tmp_path):
    """Even if model passes depth=999, we cap at config.max_depth."""
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "c").mkdir()
    (tmp_path / "a" / "b" / "c" / "file").write_text("")

    tool = LSTool(max_depth=2, show_hidden=False)  # max 2
    result = tool.execute({"path": str(tmp_path), "depth": 999})
    assert "a/b/" in result
    assert "a/b/c/" not in result  # capped at depth 2


def test_ls_empty_dir(tmp_path):
    tool = LSTool(max_depth=1, show_hidden=False)
    result = tool.execute({"path": str(tmp_path)})
    assert "(empty)" in result


def test_ls_nonexistent_path_raises(tmp_path):
    tool = LSTool(max_depth=1, show_hidden=False)
    with pytest.raises(ToolError, match="does not exist"):
        tool.execute({"path": str(tmp_path / "missing")})


def test_ls_file_path_raises(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("")
    tool = LSTool(max_depth=1, show_hidden=False)
    with pytest.raises(ToolError, match="not a directory"):
        tool.execute({"path": str(f)})


def test_ls_input_schema_includes_max_depth_in_description():
    tool = LSTool(max_depth=7, show_hidden=False)
    schema = tool.input_schema
    assert "max_depth=7" in schema.properties["depth"]["description"]


def test_ls_from_config_requires_keys():
    with pytest.raises(ValueError, match="missing required key.*max_depth"):
        LSTool.from_config({"show_hidden": False})
    with pytest.raises(ValueError, match="missing required key.*show_hidden"):
        LSTool.from_config({"max_depth": 2})


def test_ls_from_config_happy_path():
    tool = LSTool.from_config({"max_depth": 4, "show_hidden": True})
    assert tool._max_depth == 4
    assert tool._show_hidden is True


# ── Tool factory ───────────────────────────────────────────────────────────


def test_build_constructs_enabled_tools():
    cfg = ToolsConfig(
        enabled=["ls"],
        config={"ls": {"max_depth": 3, "show_hidden": False}},
    )
    reg = build(cfg)
    assert "ls" in reg
    assert len(reg) == 1


def test_build_unknown_tool_raises():
    cfg = ToolsConfig(enabled=["nope"], config={})
    with pytest.raises(ValueError, match="unknown tool 'nope'"):
        build(cfg)


def test_build_missing_per_tool_config_raises_from_tool():
    """If a tool's config block is missing required keys, the tool's
    from_config raises — surfaces as a ValueError at startup."""
    cfg = ToolsConfig(enabled=["ls"], config={"ls": {}})
    with pytest.raises(ValueError):
        build(cfg)


def test_build_empty_enabled_returns_empty_registry():
    cfg = ToolsConfig(enabled=[], config={})
    reg = build(cfg)
    assert len(reg) == 0
