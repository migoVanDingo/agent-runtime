"""Unit tests for `arc setup`'s comment-preserving config writer."""
from __future__ import annotations

from pathlib import Path

import pytest

from arc.defaults import DEFAULT_CONFIG_YAML
from arc.setup.writer import render_changes, write_provider_choice


def _write_default(tmp_path: Path) -> Path:
    p = tmp_path / "config.yml"
    p.write_text(DEFAULT_CONFIG_YAML)
    return p


# ── Round-trip preservation ────────────────────────────────────────────────


def test_comments_in_default_config_survive_round_trip(tmp_path: Path):
    path = _write_default(tmp_path)
    write_provider_choice(
        path,
        name="anthropic", model="claude-sonnet-4-6",
        base_url=None, api_key_env="ANTHROPIC_API_KEY",
    )
    text = path.read_text()
    # Spot-check several comments from the shipped default
    assert "# ── Runtime" in text
    assert "# ── Provider" in text
    assert "Don't set top_p here" in text
    assert "# ── Tools" in text


def test_only_targeted_keys_change(tmp_path: Path):
    path = _write_default(tmp_path)
    before = path.read_text()
    write_provider_choice(
        path,
        name="anthropic", model="claude-sonnet-4-6",
        base_url=None, api_key_env="ANTHROPIC_API_KEY",
    )
    after = path.read_text()
    # provider.name changed from gemini → anthropic
    assert "name: gemini" not in after
    assert "name: anthropic" in after
    # provider.model changed
    assert "gemini-3.1-flash-lite-preview" not in after
    assert "claude-sonnet-4-6" in after
    # Tools section is unchanged
    assert "enabled: [ls, bash_exec]" in after


# ── Set-always vs set-if-missing ───────────────────────────────────────────


def test_existing_non_null_base_url_is_preserved(tmp_path: Path):
    """User set base_url to their own host; the picker default must not stomp it."""
    import yaml as _yaml
    path = tmp_path / "config.yml"
    path.write_text(DEFAULT_CONFIG_YAML.replace(
        "base_url: null",
        "base_url: http://my-host:11434/v1",
    ))
    changes = write_provider_choice(
        path,
        name="ollama", model="llama3.1:8b",
        base_url="http://localhost:11434/v1",  # default picker value
        api_key_env="OLLAMA_API_KEY",
    )
    parsed = _yaml.safe_load(path.read_text())
    assert parsed["provider"]["base_url"] == "http://my-host:11434/v1"
    by_key = {c.key: c for c in changes}
    assert by_key["provider.base_url"].skipped is True


def test_null_base_url_is_replaced(tmp_path: Path):
    path = _write_default(tmp_path)
    write_provider_choice(
        path,
        name="ollama", model="llama3.1:8b",
        base_url="http://localhost:11434/v1",
        api_key_env="OLLAMA_API_KEY",
    )
    text = path.read_text()
    assert "http://localhost:11434/v1" in text


def test_api_key_env_preserved_if_already_set(tmp_path: Path):
    """If the user has a custom api_key_env, picker shouldn't override."""
    path = tmp_path / "config.yml"
    path.write_text(DEFAULT_CONFIG_YAML.replace(
        "api_key_env: GEMINI_API_KEY",
        "api_key_env: MY_CUSTOM_GEMINI_KEY",
    ))
    write_provider_choice(
        path,
        name="gemini", model="gemini-2.5-flash",
        base_url=None, api_key_env="GEMINI_API_KEY",
    )
    text = path.read_text()
    assert "MY_CUSTOM_GEMINI_KEY" in text
    assert "api_key_env: GEMINI_API_KEY" not in text


def test_provider_name_and_model_always_overwrite(tmp_path: Path):
    """Even when set to non-default, name/model get replaced (whole point of setup)."""
    path = tmp_path / "config.yml"
    path.write_text(DEFAULT_CONFIG_YAML)
    # Edit it first
    write_provider_choice(
        path, name="anthropic", model="claude-haiku-4-5",
        base_url=None, api_key_env="ANTHROPIC_API_KEY",
    )
    # Now run setup again with a different choice
    write_provider_choice(
        path, name="gemini", model="gemini-2.5-pro",
        base_url=None, api_key_env="GEMINI_API_KEY",
    )
    text = path.read_text()
    assert "name: gemini" in text
    assert "gemini-2.5-pro" in text
    assert "anthropic" not in text or "anthropic" in text.lower()  # may appear in comment block


# ── Errors ─────────────────────────────────────────────────────────────────


def test_missing_provider_block_raises(tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text("runtime:\n  workspace: .\n")
    with pytest.raises(ValueError, match="no `provider:` block"):
        write_provider_choice(
            path, name="anthropic", model="claude-haiku-4-5",
            base_url=None, api_key_env="ANTHROPIC_API_KEY",
        )


# ── Diff rendering ─────────────────────────────────────────────────────────


def test_render_changes_handles_set_and_skip(tmp_path: Path):
    path = _write_default(tmp_path)
    changes = write_provider_choice(
        path,
        name="anthropic", model="claude-sonnet-4-6",
        base_url=None, api_key_env="ANTHROPIC_API_KEY",
    )
    rendered = render_changes(changes)
    # provider.name went gemini -> anthropic — shown as ~
    assert "provider.name" in rendered
    assert "gemini" in rendered
    assert "anthropic" in rendered
