"""Auto-launch behavior of `arc setup` (interactive path drops into TUI)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from arc.cli import _cmd_setup


def _stub_setup_result(provider: str = "anthropic", model: str = "claude-haiku-4-5",
                       warning: str | None = None):
    from arc.setup.picker import SetupResult
    return SetupResult(
        provider=provider, model=model,
        config_path=Path("/tmp/c.yml"),
        diff_text="  ~ provider.name: 'gemini' → 'anthropic'",
        api_key_warning=warning,
    )


# ── Scripted mode never launches ──────────────────────────────────────────


def test_scripted_setup_does_not_launch(capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with patch("arc.setup.run_setup", return_value=_stub_setup_result()) as mock_setup, \
         patch("arc.cli._cmd_interactive") as mock_tui:
        rc = _cmd_setup(
            home_override=None,
            provider="anthropic", model="claude-haiku-4-5",
            print_only=False, no_launch=False,
        )
    assert rc == 0
    mock_setup.assert_called_once()
    mock_tui.assert_not_called()


def test_scripted_provider_only_errors(capsys):
    """--model without --provider returns 2 without invoking setup."""
    with patch("arc.setup.run_setup") as mock_setup, \
         patch("arc.cli._cmd_interactive") as mock_tui:
        rc = _cmd_setup(
            home_override=None,
            provider=None, model="claude",
            print_only=False, no_launch=False,
        )
    assert rc == 2
    mock_setup.assert_not_called()
    mock_tui.assert_not_called()


# ── Interactive mode launches by default ──────────────────────────────────


def test_interactive_setup_launches_tui(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with patch("arc.setup.run_setup", return_value=_stub_setup_result()), \
         patch("arc.cli._cmd_interactive", return_value=0) as mock_tui:
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=False,
        )
    assert rc == 0
    mock_tui.assert_called_once_with(None)


def test_interactive_setup_returns_tui_exit_code(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with patch("arc.setup.run_setup", return_value=_stub_setup_result()), \
         patch("arc.cli._cmd_interactive", return_value=42):
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=False,
        )
    assert rc == 42


# ── --no-launch suppresses ────────────────────────────────────────────────


def test_no_launch_flag_skips_tui(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with patch("arc.setup.run_setup", return_value=_stub_setup_result()), \
         patch("arc.cli._cmd_interactive") as mock_tui:
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=True,
        )
    assert rc == 0
    mock_tui.assert_not_called()


# ── --print never launches ────────────────────────────────────────────────


def test_print_only_skips_tui(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with patch("arc.setup.run_setup", return_value=_stub_setup_result()), \
         patch("arc.cli._cmd_interactive") as mock_tui:
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=True, no_launch=False,
        )
    assert rc == 0
    mock_tui.assert_not_called()


# ── Missing api key skips launch ──────────────────────────────────────────


def test_api_key_missing_warning_skips_launch(capsys):
    """If the picker flagged a missing api key, don't auto-launch — the
    session would fail immediately at provider construction."""
    with patch("arc.setup.run_setup",
               return_value=_stub_setup_result(warning="ANTHROPIC_API_KEY not set")), \
         patch("arc.cli._cmd_interactive") as mock_tui:
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=False,
        )
    assert rc == 0
    mock_tui.assert_not_called()
    err = capsys.readouterr().err
    assert "skipping launch" in err


# ── Abort propagates ──────────────────────────────────────────────────────


def test_setup_abort_does_not_launch():
    with patch("arc.setup.run_setup", side_effect=SystemExit("aborted")), \
         patch("arc.cli._cmd_interactive") as mock_tui:
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=False,
        )
    assert rc == 1
    mock_tui.assert_not_called()
