"""`arc setup` launch behavior.

Two paths:
  - default (no flags)              → opens the setup hub (0023). Does NOT
                                       auto-launch a TUI session — the user
                                       navigates from the hub.
  - --picker / scripted / --print   → preserves the 0017 behavior:
                                       walks the picker, then optionally
                                       drops into a TUI session.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from arc.cli import _cmd_setup


def _HubOK(launch: bool = False):
    from arc.setup.hub import HubResult
    return HubResult(rc=0, launch_session=launch)


def _stub_setup_result(provider: str = "anthropic", model: str = "claude-haiku-4-5",
                       warning: str | None = None):
    from arc.setup.picker import SetupResult
    return SetupResult(
        provider=provider, model=model,
        config_path=Path("/tmp/c.yml"),
        diff_text="  ~ provider.name: 'gemini' → 'anthropic'",
        api_key_warning=warning,
    )


# ── Default path opens the hub ────────────────────────────────────────────


def test_no_flags_opens_setup_hub(monkeypatch):
    """`arc setup` (no flags) routes to run_hub, not the picker."""
    with patch("arc.setup.hub.run_hub", return_value=_HubOK()) as mock_hub, \
         patch("arc.bootstrap.bootstrap") as mock_boot, \
         patch("arc.setup.run_setup") as mock_setup, \
         patch("arc.cli._cmd_interactive") as mock_tui:
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=False,
        )
    assert rc == 0
    mock_hub.assert_called_once()
    mock_setup.assert_not_called()
    mock_tui.assert_not_called()


def test_hub_returns_its_exit_code():
    from arc.setup.hub import HubResult
    with patch("arc.setup.hub.run_hub", return_value=HubResult(rc=7, launch_session=False)), \
         patch("arc.bootstrap.bootstrap"):
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=False,
        )
    assert rc == 7


def test_hub_esc_launches_session_after_setup():
    """`arc setup` → hub → esc-from-sidebar → drop into _cmd_interactive."""
    with patch("arc.setup.hub.run_hub", return_value=_HubOK(launch=True)), \
         patch("arc.bootstrap.bootstrap"), \
         patch("arc.cli._cmd_interactive", return_value=0) as mock_tui:
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=False,
        )
    assert rc == 0
    mock_tui.assert_called_once()


def test_section_flag_passes_through(monkeypatch):
    with patch("arc.setup.hub.run_hub", return_value=_HubOK()) as mock_hub, \
         patch("arc.bootstrap.bootstrap"):
        _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=False,
            section="themes",
        )
    assert mock_hub.call_args.kwargs["initial_section"] == "themes"


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


# ── --picker preserves the classic launch-after-pick contract ─────────────


def test_picker_flag_launches_tui_after_pick(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with patch("arc.setup.run_setup", return_value=_stub_setup_result()), \
         patch("arc.cli._cmd_interactive", return_value=0) as mock_tui:
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=False,
            hub=False,
        )
    assert rc == 0
    mock_tui.assert_called_once_with(None)


def test_picker_returns_tui_exit_code(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with patch("arc.setup.run_setup", return_value=_stub_setup_result()), \
         patch("arc.cli._cmd_interactive", return_value=42):
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=False,
            hub=False,
        )
    assert rc == 42


# ── --no-launch suppresses (in picker mode) ───────────────────────────────


def test_no_launch_flag_skips_tui_in_picker_mode(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with patch("arc.setup.run_setup", return_value=_stub_setup_result()), \
         patch("arc.cli._cmd_interactive") as mock_tui:
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=True,
            hub=False,
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


# ── Missing api key skips launch (in picker mode) ─────────────────────────


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
            hub=False,
        )
    assert rc == 0
    mock_tui.assert_not_called()
    err = capsys.readouterr().err
    assert "skipping launch" in err


# ── Abort propagates (in picker mode) ─────────────────────────────────────


def test_setup_abort_does_not_launch():
    with patch("arc.setup.run_setup", side_effect=SystemExit("aborted")), \
         patch("arc.cli._cmd_interactive") as mock_tui:
        rc = _cmd_setup(
            home_override=None,
            provider=None, model=None,
            print_only=False, no_launch=False,
            hub=False,
        )
    assert rc == 1
    mock_tui.assert_not_called()
