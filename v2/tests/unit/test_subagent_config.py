"""subagents: config block parsing + writer round-trip."""
from __future__ import annotations

from pathlib import Path

import pytest

from arc.config import _parse_subagents, SubAgentsConfig
from arc.setup.writer import write_subagent_enablement


def test_missing_block_parses_to_empty():
    cfg = _parse_subagents(None)
    assert isinstance(cfg, SubAgentsConfig)
    assert cfg.entries == []


def test_single_override_entry():
    cfg = _parse_subagents({
        "example_log_grepper": {"model": "claude-sonnet-4-6", "timeout_s": 60},
    })
    assert len(cfg.entries) == 1
    e = cfg.entries[0]
    assert e.name == "example_log_grepper"
    assert e.enabled is True   # default
    assert e.fields == {"model": "claude-sonnet-4-6", "timeout_s": 60}


def test_explicit_disabled():
    cfg = _parse_subagents({"foo": {"enabled": False}})
    assert cfg.entries[0].enabled is False
    assert cfg.entries[0].fields == {}


def test_complete_new_spec_block():
    cfg = _parse_subagents({
        "my_classifier": {
            "description": "classify",
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "system_prompt": "be precise",
            "tools": ["bash"],
            "enabled": True,
        },
    })
    e = cfg.entries[0]
    assert e.enabled is True
    assert e.fields["description"] == "classify"
    assert e.fields["tools"] == ["bash"]


def test_non_mapping_raises():
    with pytest.raises(ValueError, match="must be a mapping"):
        _parse_subagents([{"foo": "bar"}])  # type: ignore[arg-type]


def test_as_overrides_shape():
    cfg = _parse_subagents({"a": {"model": "m1"}, "b": {"enabled": False, "model": "m2"}})
    ov = cfg.as_overrides()
    assert ov == {
        "a": {"model": "m1", "enabled": True},
        "b": {"model": "m2", "enabled": False},
    }


# ── Writer round-trip ─────────────────────────────────────────────────────

_MINIMAL_CONFIG_WITH_SUBAGENTS = """\
runtime: {}
provider: {}
tools: {}
plugins:
  failure_threshold: 3
  exception_message_max_chars: 500
  enabled: []
# pre-existing comment above subagents block
subagents:
  example_spec:
    model: claude-haiku-4-5  # in-line comment, must survive
tui: {}
bootstrap: {}
"""


def test_writer_creates_block_when_missing(tmp_path: Path):
    p = tmp_path / "config.yml"
    p.write_text("subagents:\nrest: 1\n", encoding="utf-8")
    changes = write_subagent_enablement(p, name="new_spec", enabled=True)
    assert any("subagents.new_spec" in c.key for c in changes)
    text = p.read_text()
    assert "new_spec" in text
    assert "enabled" in text


def test_writer_preserves_comments(tmp_path: Path):
    p = tmp_path / "config.yml"
    p.write_text(_MINIMAL_CONFIG_WITH_SUBAGENTS, encoding="utf-8")
    write_subagent_enablement(p, name="example_spec", enabled=False)
    text = p.read_text()
    assert "pre-existing comment above subagents block" in text
    assert "in-line comment, must survive" in text
    # Existing model field was not touched
    assert "claude-haiku-4-5" in text


def test_writer_toggle_existing(tmp_path: Path):
    p = tmp_path / "config.yml"
    p.write_text(_MINIMAL_CONFIG_WITH_SUBAGENTS, encoding="utf-8")
    # First toggle: add enabled=false
    changes = write_subagent_enablement(p, name="example_spec", enabled=False)
    assert any("enabled" in c.key for c in changes)
    # Second toggle back to true
    changes = write_subagent_enablement(p, name="example_spec", enabled=True)
    assert any("enabled" in c.key for c in changes)
    text = p.read_text()
    assert "enabled: true" in text.lower() or "enabled: True" in text
