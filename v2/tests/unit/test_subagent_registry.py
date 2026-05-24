"""SubAgentRegistry discovery and override merging."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from arc.runtime.subagents.registry import SubAgentBuildContext, SubAgentRegistry
from arc.subagent_api import SubAgentSpec


def _builtin(**kwargs) -> SubAgentSpec:
    base = dict(
        name="_test_echo", description="echo", provider="anthropic",
        model="claude-haiku-4-5", system_prompt="echo", source="builtin",
    )
    base.update(kwargs)
    return SubAgentSpec(**base)


@dataclass(frozen=True)
class _FakeEP:
    """Mimics importlib.metadata.EntryPoint shape just enough for the loader."""
    name: str
    value: str
    _builder: object
    group: str = "arc.subagents"
    def load(self):
        return self._builder


def test_builtin_discovery():
    reg = SubAgentRegistry(
        builtins={"_test_echo": _builtin()},
        entry_point_loader=lambda: [],
    )
    reg.discover({})
    assert "_test_echo" in reg.all_specs()
    assert reg.is_enabled("_test_echo")


def test_plugin_entry_point_discovery():
    def builder(config, ctx):
        return SubAgentSpec(
            name="plugin_spec", description="d", provider="gemini",
            model="gemini-2.5-flash", system_prompt="p",
        )
    reg = SubAgentRegistry(
        builtins={},
        entry_point_loader=lambda: [_FakeEP("plugin_spec", "mod:build", builder)],
    )
    reg.discover({})
    assert "plugin_spec" in reg.all_specs()
    assert reg.get("plugin_spec").source == "plugin"


def test_plugin_collision_with_builtin_drops_plugin():
    """Built-ins win on name collision; the plugin is reported in conflicts."""
    def builder(config, ctx):
        return SubAgentSpec(
            name="_test_echo", description="hijack", provider="x",
            model="y", system_prompt="z",
        )
    reg = SubAgentRegistry(
        builtins={"_test_echo": _builtin(description="original")},
        entry_point_loader=lambda: [_FakeEP("_test_echo", "mod:build", builder)],
    )
    report = reg.discover({})
    assert reg.get("_test_echo").description == "original"
    assert len(report.conflicts) == 1
    assert report.conflicts[0].name == "_test_echo"
    assert report.conflicts[0].conflicts_with == "builtin:_test_echo"


def test_load_failure_isolated():
    """A builder that raises gets recorded as a failure, others still load."""
    def good_builder(config, ctx):
        return SubAgentSpec(name="good", description="d", provider="anthropic",
                            model="m", system_prompt="p")

    def bad_builder(config, ctx):
        raise RuntimeError("boom")

    reg = SubAgentRegistry(
        builtins={},
        entry_point_loader=lambda: [
            _FakeEP("bad", "x:build", bad_builder),
            _FakeEP("good", "y:build", good_builder),
        ],
    )
    report = reg.discover({})
    assert "good" in reg.all_specs()
    assert "bad" not in reg.all_specs()
    assert len(report.failures) == 1
    assert "boom" in report.failures[0].error


def test_config_override_on_plugin_spec():
    """Field-level override merges on top; source flips to 'config'."""
    def builder(config, ctx):
        return SubAgentSpec(
            name="grepper", description="grep", provider="anthropic",
            model="claude-haiku-4-5", system_prompt="search",
            timeout_s=90.0,
        )
    reg = SubAgentRegistry(
        builtins={},
        entry_point_loader=lambda: [_FakeEP("grepper", "g:build", builder)],
    )
    reg.discover({"grepper": {"model": "claude-sonnet-4-6", "timeout_s": 600}})
    spec = reg.get("grepper")
    assert spec.model == "claude-sonnet-4-6"
    assert spec.timeout_s == 600
    assert spec.source == "config"  # config touched it


def test_config_only_new_spec():
    """User-defined config spec needs all required fields."""
    reg = SubAgentRegistry(
        builtins={},
        entry_point_loader=lambda: [],
    )
    reg.discover({
        "my_custom": {
            "description": "do a thing",
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "system_prompt": "you are helpful",
            "tools": ["bash"],
            "timeout_s": 30.0,
        },
    })
    spec = reg.get("my_custom")
    assert spec.tools == ("bash",)
    assert spec.timeout_s == 30.0
    assert spec.source == "config"


def test_config_only_missing_required_fields_raises():
    reg = SubAgentRegistry(builtins={}, entry_point_loader=lambda: [])
    with pytest.raises(ValueError, match="missing required fields"):
        reg.discover({"broken": {"description": "incomplete"}})


def test_config_enabled_false_marks_disabled_but_keeps_discovered():
    """Disabled specs are still in all_specs(); just absent from enabled_specs()."""
    reg = SubAgentRegistry(
        builtins={"_test_echo": _builtin()},
        entry_point_loader=lambda: [],
    )
    reg.discover({"_test_echo": {"enabled": False}})
    assert "_test_echo" in reg.all_specs()
    assert "_test_echo" not in reg.enabled_specs()
    assert reg.is_enabled("_test_echo") is False
