"""SubAgentSpec dataclass behavior + override merge semantics."""
from __future__ import annotations

import dataclasses

import pytest

from arc.subagent_api import SubAgentSpec


def _spec(**overrides) -> SubAgentSpec:
    base = dict(
        name="x", description="d", provider="anthropic",
        model="claude-haiku-4-5", system_prompt="be helpful",
    )
    base.update(overrides)
    return SubAgentSpec(**base)


def test_spec_is_frozen():
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.model = "other"  # type: ignore[misc]


def test_defaults_match_design():
    spec = _spec()
    assert spec.tools == ()
    assert spec.timeout_s == 300.0
    assert spec.max_turns == 25
    assert spec.max_dispatches_per_session == 5
    assert spec.max_consecutive_failures == 2
    assert spec.max_transient_retries == 2
    assert spec.expected_output is None
    assert spec.source == "plugin"


def test_equality_by_fields():
    a = _spec()
    b = _spec()
    assert a == b
    assert a != _spec(model="other")


def test_merged_with_returns_new_instance():
    spec = _spec()
    merged = spec.merged_with({"model": "claude-sonnet-4-6"})
    assert merged is not spec
    assert merged.model == "claude-sonnet-4-6"
    assert spec.model == "claude-haiku-4-5"  # original untouched


def test_merged_with_normalizes_tools_to_tuple():
    spec = _spec()
    merged = spec.merged_with({"tools": ["bash", "read"]})
    assert merged.tools == ("bash", "read")
    assert isinstance(merged.tools, tuple)


def test_merged_with_rejects_unknown_field():
    spec = _spec()
    with pytest.raises(ValueError, match="unknown override fields"):
        spec.merged_with({"definitely_not_a_field": 42})


def test_merged_with_empty_returns_self():
    spec = _spec()
    assert spec.merged_with({}) is spec
