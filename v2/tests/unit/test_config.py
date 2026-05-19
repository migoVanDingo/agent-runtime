"""Tests for config loading + validation."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arc.bootstrap import bootstrap
from arc.config import ConfigError, load
from arc.defaults import DEFAULT_CONFIG_YAML


@pytest.fixture
def bootstrapped(tmp_path):
    """A tmp_path that's been bootstrapped, ready to load config from."""
    home = tmp_path / "arc-home"
    bootstrap(home)
    return home


# ── Happy path ──────────────────────────────────────────────────────────────


def test_default_config_loads_cleanly(bootstrapped):
    cfg = load(bootstrapped / "config.yml")
    assert cfg.runtime.workspace == "."
    assert cfg.runtime.max_iterations == 50
    assert cfg.provider.name == "gemini"
    assert cfg.provider.model == "gemini-3.1-flash-lite-preview"
    assert cfg.provider.api_key_env == "GEMINI_API_KEY"
    assert cfg.provider.retry.max_attempts == 3
    assert cfg.tools.enabled == ["ls", "bash_exec"]
    assert cfg.tools.config["ls"]["max_depth"] == 2
    assert cfg.tui.inline_mode is True
    assert cfg.source_path == bootstrapped / "config.yml"


def test_active_plugins_filters_disabled(bootstrapped):
    cfg = load(bootstrapped / "config.yml")
    active = cfg.plugins.active()
    names = [p.name for p in active]
    # All three plugins are enabled by default as of phase 2.1.5
    assert "jsonl-recorder" in names
    assert "guard" in names
    assert "pause-resume" in names


def test_filtering_works_when_a_plugin_is_explicitly_disabled(tmp_path):
    """Just verify the filtering mechanism works — pause-resume happens to
    be enabled by default now, so test with a synthetic disabled entry."""
    import yaml
    p = tmp_path / "c.yml"
    full = yaml.safe_load(DEFAULT_CONFIG_YAML)
    # Mark jsonl-recorder explicitly disabled
    for entry in full["plugins"]["enabled"]:
        if entry["name"] == "jsonl-recorder":
            entry["enabled"] = False
    p.write_text(yaml.safe_dump(full))

    cfg = load(p)
    names = [pl.name for pl in cfg.plugins.active()]
    assert "jsonl-recorder" not in names
    assert "guard" in names


def test_plugin_policy_loaded_from_config(bootstrapped):
    cfg = load(bootstrapped / "config.yml")
    assert cfg.plugins.failure_threshold == 3
    assert cfg.plugins.exception_message_max_chars == 500


def test_missing_plugin_policy_keys_raises(tmp_path):
    p = tmp_path / "bad.yml"
    full = yaml.safe_load(DEFAULT_CONFIG_YAML)
    del full["plugins"]["failure_threshold"]
    p.write_text(yaml.safe_dump(full))
    with pytest.raises(ConfigError, match="plugins.*missing required keys.*failure_threshold"):
        load(p)


def test_default_yaml_round_trips(tmp_path):
    """Sanity check: the default YAML constant parses without error."""
    p = tmp_path / "c.yml"
    p.write_text(DEFAULT_CONFIG_YAML)
    cfg = load(p)
    assert cfg.provider.params["temperature"] == 0


# ── Error paths ─────────────────────────────────────────────────────────────


def test_missing_file_raises_with_helpful_message(tmp_path):
    with pytest.raises(ConfigError, match=r"arc bootstrap"):
        load(tmp_path / "nope.yml")


def test_invalid_yaml_raises(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text("runtime:\n  workspace: [unclosed")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load(p)


def test_non_mapping_top_level_raises(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text("- list\n- at top level\n")
    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        load(p)


def test_unknown_top_level_key_raises(tmp_path):
    p = tmp_path / "bad.yml"
    full = yaml.safe_load(DEFAULT_CONFIG_YAML)
    full["mystery_section"] = {}
    p.write_text(yaml.safe_dump(full))
    with pytest.raises(ConfigError, match="unknown top-level keys.*mystery_section"):
        load(p)


def test_missing_required_section_raises(tmp_path):
    p = tmp_path / "bad.yml"
    full = yaml.safe_load(DEFAULT_CONFIG_YAML)
    del full["provider"]
    p.write_text(yaml.safe_dump(full))
    with pytest.raises(ConfigError, match="missing required sections.*provider"):
        load(p)


def test_missing_subkey_in_required_section_raises(tmp_path):
    p = tmp_path / "bad.yml"
    full = yaml.safe_load(DEFAULT_CONFIG_YAML)
    del full["provider"]["model"]
    p.write_text(yaml.safe_dump(full))
    with pytest.raises(ConfigError, match="provider.*missing required keys.*model"):
        load(p)


def test_plugin_entry_without_name_raises(tmp_path):
    p = tmp_path / "bad.yml"
    full = yaml.safe_load(DEFAULT_CONFIG_YAML)
    full["plugins"]["enabled"].append({"config": {}})  # no name
    p.write_text(yaml.safe_dump(full))
    with pytest.raises(ConfigError, match="missing 'name'"):
        load(p)


# ── Behavioral details ─────────────────────────────────────────────────────


def test_plugin_enabled_defaults_to_true_when_omitted(tmp_path):
    """Listing a plugin without `enabled:` means it's on (opt-out, not opt-in)."""
    p = tmp_path / "c.yml"
    full = yaml.safe_load(DEFAULT_CONFIG_YAML)
    # Add a plugin with no enabled key
    full["plugins"]["enabled"].append({"name": "implicit", "config": {}, "hooks_order": {}})
    p.write_text(yaml.safe_dump(full))

    cfg = load(p)
    implicit = next(pl for pl in cfg.plugins.enabled if pl.name == "implicit")
    assert implicit.enabled is True


def test_config_is_frozen(bootstrapped):
    cfg = load(bootstrapped / "config.yml")
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        cfg.runtime.workspace = "/elsewhere"  # type: ignore[misc]


def test_provider_params_passthrough(bootstrapped):
    """Params should be a raw dict — providers consume them verbatim."""
    cfg = load(bootstrapped / "config.yml")
    assert cfg.provider.params == {"temperature": 0, "max_tokens": 4096, "top_p": 1.0}
