"""Tests for the setup hub structure + section wiring (0023).

We don't actually run the prompt_toolkit Application — that requires a TTY.
We do verify section assembly, initial-section routing, and that each
built-in section produces a Section with the right shape.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def hub_ctx(tmp_path):
    """Build a HubContext over a fresh bootstrapped ARC_HOME."""
    from arc.bootstrap import bootstrap
    from arc.setup.hub import HubContext
    home = tmp_path / "arc-home"
    bootstrap(home)
    return HubContext(
        home=home,
        config_path=home / "config.yml",
        catalog_path=home / "catalog.yml",
        llm_servers_path=home / "llm_servers.yml",
    )


# ── Sections list ─────────────────────────────────────────────────────────


def test_hub_builds_with_expected_sections(hub_ctx):
    from arc.setup.hub import Hub
    hub = Hub(hub_ctx)
    names = [s.name for s in hub._sections]
    assert names == [
        "provider", "plugins", "mcp", "subagents", "replay", "llm",
        "themes", "status", "wipe", "config",
    ]


def test_initial_section_routes_by_name(hub_ctx):
    from arc.setup.hub import Hub
    hub = Hub(hub_ctx, initial_section="themes")
    assert hub._sections[hub._index].name == "themes"


def test_initial_section_unknown_falls_back_to_first(hub_ctx):
    from arc.setup.hub import Hub
    hub = Hub(hub_ctx, initial_section="does-not-exist")
    assert hub._index == 0


# ── Each section is structurally valid ────────────────────────────────────


def test_every_section_has_required_fields(hub_ctx):
    from arc.setup.hub import Hub
    hub = Hub(hub_ctx)
    for sec in hub._sections:
        assert sec.name
        assert sec.title
        assert callable(sec.summary)
        # summary() must not raise on a fresh bootstrapped home
        sec.summary()
        assert sec.container is not None


# ── Navigation state ──────────────────────────────────────────────────────


def test_move_wraps_around(hub_ctx):
    from arc.setup.hub import Hub
    hub = Hub(hub_ctx)
    n = len(hub._sections)
    hub._move(-1)
    assert hub._index == n - 1
    hub._move(+1)
    assert hub._index == 0


# ── Themes section save path ──────────────────────────────────────────────


def test_themes_section_writes_to_config(hub_ctx):
    """Save path of the themes section writes a fresh tui.theme value."""
    from arc.setup.sections import themes as themes_section
    themes_section._write_theme(hub_ctx.config_path, "dracula")
    body = hub_ctx.config_path.read_text(encoding="utf-8")
    assert "theme: dracula" in body


def test_themes_section_round_trip_preserves_comments(hub_ctx):
    """The comment-preserving write doesn't strip top-of-file comments."""
    body = hub_ctx.config_path.read_text(encoding="utf-8")
    # Default config has comments — verify at least one survives the round trip
    assert "#" in body
    from arc.setup.sections import themes as themes_section
    themes_section._write_theme(hub_ctx.config_path, "gruvbox")
    after = hub_ctx.config_path.read_text(encoding="utf-8")
    assert "#" in after
    assert "theme: gruvbox" in after


# ── Wipe section dispatch ─────────────────────────────────────────────────


def test_wipe_section_no_targets_yields_noop_message(hub_ctx, tmp_path):
    """Empty checked set should not error; just sets the status message."""
    from arc.setup.sections import wipe as wipe_section
    wipe_section._state["checked"] = set()
    wipe_section._state["armed"] = True
    wipe_section._do_wipe(hub_ctx)
    assert "nothing" in wipe_section._state["msg"].lower() or wipe_section._state["msg"].startswith("wiped")
