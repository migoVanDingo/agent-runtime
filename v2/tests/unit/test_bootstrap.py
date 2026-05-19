"""Tests for home dir resolution + bootstrap."""
from __future__ import annotations

from pathlib import Path

import pytest

from arc.bootstrap import (
    DEFAULT_HOME,
    ARC_HOME,
    bootstrap,
    paths_for,
    resolve_home,
)


def test_resolve_home_defaults_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv(ARC_HOME, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_home() == (tmp_path / ".arc").resolve()


def test_resolve_home_defaults_when_env_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv(ARC_HOME, "")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_home() == (tmp_path / ".arc").resolve()


def test_resolve_home_uses_env_full_path(monkeypatch, tmp_path):
    monkeypatch.setenv(ARC_HOME, str(tmp_path / "arc-here"))
    assert resolve_home() == (tmp_path / "arc-here").resolve()


def test_resolve_home_expands_tilde_in_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(ARC_HOME, "~/Projects/p1/.arc")
    assert resolve_home() == (tmp_path / "Projects/p1/.arc").resolve()


def test_resolve_home_expands_env_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PROJ", "myproj")
    monkeypatch.setenv(ARC_HOME, "$HOME/$PROJ/.arc")
    assert resolve_home() == (tmp_path / "myproj/.arc").resolve()


def test_cli_override_wins_over_env(monkeypatch, tmp_path):
    monkeypatch.setenv(ARC_HOME, "/should/be/ignored")
    override = tmp_path / "explicit"
    assert resolve_home(cli_override=str(override)) == override.resolve()


def test_tilde_in_cli_override(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(ARC_HOME, raising=False)
    result = resolve_home(cli_override="~/explicit-arc")
    assert result == (tmp_path / "explicit-arc").resolve()


def test_bootstrap_creates_full_layout(tmp_path):
    home = tmp_path / "arc-home"
    result = bootstrap(home)

    assert result.created_home
    assert result.wrote_config
    assert result.created_sessions_dir
    assert result.created_sessions_index
    assert result.changed_anything

    p = paths_for(home)
    assert p.home.is_dir()
    assert p.config_file.is_file()
    assert p.sessions_dir.is_dir()
    assert p.sessions_index.is_file()

    # Default config must be valid YAML
    import yaml
    assert isinstance(yaml.safe_load(p.config_file.read_text()), dict)


def test_bootstrap_is_idempotent(tmp_path):
    home = tmp_path / "arc-home"
    bootstrap(home)
    second = bootstrap(home)

    assert not second.created_home
    assert not second.wrote_config
    assert not second.created_sessions_dir
    assert not second.created_sessions_index
    assert not second.changed_anything


def test_bootstrap_preserves_user_edits_to_config(tmp_path):
    home = tmp_path / "arc-home"
    bootstrap(home)
    p = paths_for(home)

    custom = "# my edits\nruntime:\n  workspace: ~/projects\n"
    p.config_file.write_text(custom)

    bootstrap(home)  # idempotent — should not touch
    assert p.config_file.read_text() == custom


def test_bootstrap_force_overwrites_config(tmp_path):
    home = tmp_path / "arc-home"
    bootstrap(home)
    p = paths_for(home)

    p.config_file.write_text("# my edits\n")
    result = bootstrap(home, force_config=True)

    assert result.wrote_config
    # Default content restored
    assert "provider:" in p.config_file.read_text()


def test_bootstrap_force_does_not_touch_sessions(tmp_path):
    home = tmp_path / "arc-home"
    bootstrap(home)
    p = paths_for(home)

    # Simulate an existing session
    (p.sessions_dir / "ses_old").mkdir()
    (p.sessions_dir / "ses_old" / "events.jsonl").write_text("{}\n")

    bootstrap(home, force_config=True)
    assert (p.sessions_dir / "ses_old" / "events.jsonl").exists()
