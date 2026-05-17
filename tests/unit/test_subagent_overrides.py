"""Unit tests for the 0090e per-spec config overrides."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _make_spec():
    from runtime.subagents.spec import SubAgentSpec
    return SubAgentSpec(
        name="probe",
        description="test spec",
        provider=None,
        model=None,
        timeout_seconds=300.0,
        max_iterations=20,
    )


def test_no_override_returns_identical_spec(monkeypatch):
    from runtime.subagents.runner import SubAgentRunner
    from app_config import config
    monkeypatch.setattr(config.subagents, "overrides", {}, raising=False)
    spec = _make_spec()
    merged = SubAgentRunner._merge_overrides(spec)
    assert merged is spec


def test_override_applies_provider_and_model(monkeypatch):
    from runtime.subagents.runner import SubAgentRunner
    from config.subagents import SubAgentOverride
    from app_config import config
    monkeypatch.setattr(config.subagents, "overrides", {
        "probe": SubAgentOverride(provider="anthropic", model="claude-opus-4-7"),
    }, raising=False)
    spec = _make_spec()
    merged = SubAgentRunner._merge_overrides(spec)
    assert merged.provider == "anthropic"
    assert merged.model == "claude-opus-4-7"
    # Spec defaults preserved for non-overridden fields
    assert merged.timeout_seconds == spec.timeout_seconds
    assert merged.max_iterations == spec.max_iterations


def test_override_applies_timeout_and_iters(monkeypatch):
    from runtime.subagents.runner import SubAgentRunner
    from config.subagents import SubAgentOverride
    from app_config import config
    monkeypatch.setattr(config.subagents, "overrides", {
        "probe": SubAgentOverride(timeout_seconds=1200.0, max_iterations=50),
    }, raising=False)
    spec = _make_spec()
    merged = SubAgentRunner._merge_overrides(spec)
    assert merged.timeout_seconds == 1200.0
    assert merged.max_iterations == 50
    assert merged.provider is None  # not overridden


def test_partial_override_only_changes_specified_fields(monkeypatch):
    from runtime.subagents.runner import SubAgentRunner
    from config.subagents import SubAgentOverride
    from app_config import config
    monkeypatch.setattr(config.subagents, "overrides", {
        "probe": SubAgentOverride(model="claude-haiku-4-5"),  # only model
    }, raising=False)
    spec = _make_spec()
    merged = SubAgentRunner._merge_overrides(spec)
    assert merged.model == "claude-haiku-4-5"
    assert merged.provider is None
    assert merged.timeout_seconds == 300.0


def test_subagent_config_loader_handles_missing_block():
    """When config.yml omits the subagents block, SubAgentsConfig is empty."""
    from config.subagents import SubAgentsConfig
    c = SubAgentsConfig()
    assert c.overrides == {}
    assert c.get("anything") is None
