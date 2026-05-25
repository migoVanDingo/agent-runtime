"""CLI shortcuts route into the setup hub at the right section.

`arc plugins` / `arc replay` (no id) / `arc subagents` (no action) /
`arc llm` (no action) all open the hub focused on the relevant section.
Flag-driven / sub-action invocations preserve their existing contracts.

See _design/0023-setup-hub-and-themes.md.
"""
from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest


def _ok(launch: bool = False):
    """HubResult shorthand for tests."""
    from arc.setup.hub import HubResult
    return HubResult(rc=0, launch_session=launch)


def _bootstrapped(tmp_path):
    from arc.bootstrap import bootstrap
    home = tmp_path / "arc-home"
    bootstrap(home)
    return home


# ── arc plugins ───────────────────────────────────────────────────────────


def test_arc_plugins_no_action_opens_hub(tmp_path):
    from arc.cli import _cmd_plugins
    home = _bootstrapped(tmp_path)
    with patch("arc.setup.hub.run_hub", return_value=_ok()) as mock_hub:
        rc = _cmd_plugins(home_override=str(home), action=None)
    assert rc == 0
    assert mock_hub.call_args.kwargs["initial_section"] == "plugins"


def test_arc_plugins_list_uses_non_interactive_path(tmp_path, capsys):
    from arc.cli import _cmd_plugins
    home = _bootstrapped(tmp_path)
    with patch("arc.setup.hub.run_hub") as mock_hub:
        rc = _cmd_plugins(home_override=str(home), action="list")
    assert rc == 0
    mock_hub.assert_not_called()


# ── arc subagents ─────────────────────────────────────────────────────────


def test_arc_subagents_no_action_opens_hub(tmp_path):
    from arc.cli import _cmd_subagents
    home = _bootstrapped(tmp_path)
    with patch("arc.setup.hub.run_hub", return_value=_ok()) as mock_hub:
        rc = _cmd_subagents(home_override=str(home), action=None, spec_name=None)
    assert rc == 0
    assert mock_hub.call_args.kwargs["initial_section"] == "subagents"


def test_arc_subagents_list_does_not_open_hub(tmp_path, capsys):
    from arc.cli import _cmd_subagents
    home = _bootstrapped(tmp_path)
    with patch("arc.setup.hub.run_hub") as mock_hub:
        rc = _cmd_subagents(home_override=str(home), action="list", spec_name=None)
    assert rc == 0
    mock_hub.assert_not_called()


# ── arc llm ───────────────────────────────────────────────────────────────


def test_arc_llm_no_action_opens_hub(tmp_path):
    from arc.cli import _cmd_llm
    home = _bootstrapped(tmp_path)
    ns = argparse.Namespace(llm_action=None)
    with patch("arc.setup.hub.run_hub", return_value=_ok()) as mock_hub:
        rc = _cmd_llm(home_override=str(home), args=ns)
    assert rc == 0
    assert mock_hub.call_args.kwargs["initial_section"] == "llm"


# ── arc replay ────────────────────────────────────────────────────────────


def test_arc_replay_no_session_id_opens_hub(tmp_path):
    from arc.cli import _cmd_replay_menu
    home = _bootstrapped(tmp_path)
    with patch("arc.setup.hub.run_hub", return_value=_ok()) as mock_hub:
        rc = _cmd_replay_menu(home_override=str(home))
    assert rc == 0
    assert mock_hub.call_args.kwargs["initial_section"] == "replay"


# ── launch-session-on-esc behavior ────────────────────────────────────────


def test_hub_esc_from_sidebar_launches_session(tmp_path):
    """When the hub exits with launch_session=True, CLI drops into _cmd_interactive."""
    from arc.cli import _cmd_plugins
    home = _bootstrapped(tmp_path)
    with patch("arc.setup.hub.run_hub", return_value=_ok(launch=True)), \
         patch("arc.cli._cmd_interactive", return_value=0) as mock_tui:
        rc = _cmd_plugins(home_override=str(home), action=None)
    assert rc == 0
    mock_tui.assert_called_once()


def test_hub_q_from_sidebar_does_not_launch(tmp_path):
    """launch_session=False means CLI returns without starting a session."""
    from arc.cli import _cmd_plugins
    home = _bootstrapped(tmp_path)
    with patch("arc.setup.hub.run_hub", return_value=_ok(launch=False)), \
         patch("arc.cli._cmd_interactive") as mock_tui:
        rc = _cmd_plugins(home_override=str(home), action=None)
    assert rc == 0
    mock_tui.assert_not_called()
