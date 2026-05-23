"""Integration-ish tests for the `arc setup` picker.

The interactive dialogs are mocked at the prompt_toolkit boundary; the
test exercises the surrounding wiring (provider + model → config write).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from arc.bootstrap import bootstrap, paths_for
from arc.setup.picker import run_setup


def _setup_home(tmp_path: Path) -> Path:
    bootstrap(tmp_path)
    return tmp_path


# ── Non-interactive paths (CLI flags) ──────────────────────────────────────


def test_run_setup_non_interactive_writes_provider_and_model(tmp_path: Path):
    home = _setup_home(tmp_path)
    result = run_setup(
        home=home,
        provider_override="anthropic",
        model_override="claude-haiku-4-5",
    )
    assert result.provider == "anthropic"
    assert result.model == "claude-haiku-4-5"
    text = (home / "config.yml").read_text()
    assert "name: anthropic" in text
    assert "claude-haiku-4-5" in text


def test_run_setup_unknown_provider_errors(tmp_path: Path):
    home = _setup_home(tmp_path)
    with pytest.raises(SystemExit, match="unknown provider"):
        run_setup(home=home, provider_override="bogus", model_override="x")


def test_run_setup_local_provider_sets_base_url_default(tmp_path: Path):
    home = _setup_home(tmp_path)
    result = run_setup(
        home=home,
        provider_override="ollama",
        model_override="llama3.1:8b",
    )
    assert result.provider == "ollama"
    text = (home / "config.yml").read_text()
    assert "http://localhost:11434/v1" in text
    assert "OLLAMA_API_KEY" in text


def test_run_setup_cloud_missing_api_key_emits_warning(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    home = _setup_home(tmp_path)
    result = run_setup(
        home=home,
        provider_override="anthropic",
        model_override="claude-haiku-4-5",
    )
    assert result.api_key_warning is not None
    assert "ANTHROPIC_API_KEY" in result.api_key_warning


def test_run_setup_cloud_with_api_key_no_warning(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    home = _setup_home(tmp_path)
    result = run_setup(
        home=home,
        provider_override="anthropic",
        model_override="claude-haiku-4-5",
    )
    assert result.api_key_warning is None


def test_run_setup_local_provider_no_warning_when_env_missing(tmp_path: Path, monkeypatch):
    """Local providers tolerate a missing api_key_env (Ollama doesn't validate)."""
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    home = _setup_home(tmp_path)
    result = run_setup(
        home=home,
        provider_override="ollama",
        model_override="llama3.1:8b",
    )
    assert result.api_key_warning is None


# ── Auto-bootstrap ─────────────────────────────────────────────────────────


def test_run_setup_auto_bootstraps_missing_home(tmp_path: Path):
    home = tmp_path / "fresh"
    assert not home.exists()
    run_setup(
        home=home,
        provider_override="gemini",
        model_override="gemini-2.5-flash",
    )
    p = paths_for(home)
    assert p.config_file.exists()
    assert p.catalog_file.exists()
    assert p.llm_servers_file.exists()


# ── Interactive paths (mocked dialogs) ─────────────────────────────────────


def test_run_setup_interactive_walks_provider_then_model(tmp_path: Path):
    home = _setup_home(tmp_path)

    with patch("prompt_toolkit.shortcuts.radiolist_dialog") as mock_dialog:
        mock_dialog.return_value.run.side_effect = ["anthropic", "claude-opus-4-7"]
        result = run_setup(home=home)

    assert result.provider == "anthropic"
    assert result.model == "claude-opus-4-7"


def test_run_setup_user_aborts_provider_menu(tmp_path: Path):
    home = _setup_home(tmp_path)
    with patch("prompt_toolkit.shortcuts.radiolist_dialog") as mock_dialog:
        mock_dialog.return_value.run.return_value = None  # abort
        with pytest.raises(SystemExit):
            run_setup(home=home)


def test_run_setup_manual_entry_pops_input_dialog(tmp_path: Path):
    home = _setup_home(tmp_path)
    with patch("prompt_toolkit.shortcuts.radiolist_dialog") as mock_radio, \
         patch("prompt_toolkit.shortcuts.input_dialog") as mock_input:
        mock_radio.return_value.run.side_effect = ["anthropic", "__manual__"]
        mock_input.return_value.run.return_value = "claude-future-model-9000"
        result = run_setup(home=home)
    assert result.model == "claude-future-model-9000"


# ── Print-only path ────────────────────────────────────────────────────────


def test_print_only_does_not_modify_config(tmp_path: Path, capsys):
    home = _setup_home(tmp_path)
    before = (home / "config.yml").read_text()
    run_setup(
        home=home,
        provider_override="anthropic",
        model_override="claude-haiku-4-5",
        print_only=True,
    )
    after = (home / "config.yml").read_text()
    assert before == after
    captured = capsys.readouterr()
    # The would-be YAML was printed
    assert "name: anthropic" in captured.out
    assert "claude-haiku-4-5" in captured.out
