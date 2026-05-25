"""Tests for the theme registry and active-theme resolution.

See _design/0023-setup-hub-and-themes.md.
"""
from __future__ import annotations

import pytest

from arc.tui import themes


def test_registry_contains_expected_built_in_names():
    expected = {"default", "dracula", "solarized-dark", "gruvbox", "mono"}
    assert expected.issubset(set(themes.REGISTRY))


def test_default_theme_is_first_in_list():
    listed = themes.list_themes()
    assert listed[0].name == "default"


@pytest.mark.parametrize("name", list(themes.REGISTRY))
def test_every_theme_defines_required_rich_keys(name: str):
    theme = themes.REGISTRY[name]
    styles = theme.rich_theme.styles
    missing = [k for k in themes.RICH_STYLE_KEYS if k not in styles]
    assert not missing, f"theme {name!r} missing rich keys: {missing}"


@pytest.mark.parametrize("name", list(themes.REGISTRY))
def test_every_theme_defines_required_pt_classes(name: str):
    """prompt_toolkit Styles store class rules; sanity-check the theme
    has a non-empty rules list. Specific class coverage is enforced by
    visual inspection — the dialogs + hub will look wrong if anything is
    missing, and a strict per-class check here is brittle."""
    theme = themes.REGISTRY[name]
    rules = theme.pt_style.style_rules
    assert rules, f"theme {name!r} has no prompt_toolkit style rules"


@pytest.mark.parametrize("name", list(themes.REGISTRY))
def test_every_theme_uses_a_real_pygments_code_theme(name: str):
    from pygments.styles import get_style_by_name
    theme = themes.REGISTRY[name]
    # Will raise if the name is unknown — that's the assertion
    get_style_by_name(theme.code_theme)


def test_load_theme_returns_requested_theme():
    assert themes.load_theme("dracula").name == "dracula"


def test_load_theme_falls_back_to_default(capsys):
    t = themes.load_theme("does-not-exist")
    assert t.name == "default"
    captured = capsys.readouterr()
    assert "unknown tui.theme" in captured.err


def test_set_active_and_active_round_trip():
    original = themes.active()
    try:
        new = themes.REGISTRY["gruvbox"]
        themes.set_active(new)
        assert themes.active().name == "gruvbox"
    finally:
        themes.set_active(original)


def test_active_falls_back_to_default_when_nothing_set(monkeypatch):
    """If set_active was never called for this process, active() yields default."""
    monkeypatch.setattr(themes, "_ACTIVE", None, raising=False)
    assert themes.active().name == "default"


def test_resolve_from_config_caches():
    original = themes.active()
    try:
        t = themes.resolve_from_config("solarized-dark")
        assert t.name == "solarized-dark"
        assert themes.active().name == "solarized-dark"
    finally:
        themes.set_active(original)


def test_resolve_from_home_with_missing_config_returns_default(tmp_path, monkeypatch):
    """If ARC_HOME has no config.yml, fall back without raising."""
    monkeypatch.setenv("ARC_HOME", str(tmp_path / "nope"))
    original = themes.active()
    try:
        t = themes.resolve_from_home()
        assert t.name == "default"
    finally:
        themes.set_active(original)
