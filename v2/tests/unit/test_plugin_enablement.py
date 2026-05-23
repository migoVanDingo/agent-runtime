"""Tests for first-run enablement + the plugin-management menu writer.

These tests exercise:
  - write_plugin_enablement / remove_plugin_entry (comment-preserving writer)
  - find_new_plugins / run_first_run_flow (enablement flow)
  - collect_rows / list_plugins (the menu row model)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from arc.config import PluginEntry, PluginsConfig, load
from arc.plugins.discovery import DiscoveredPlugin, DiscoveryReport
from arc.plugins.enablement import find_new_plugins, run_first_run_flow
from arc.setup.writer import (
    remove_plugin_entry,
    render_changes,
    write_plugin_enablement,
)


# ── Helpers ────────────────────────────────────────────────────────────────


_MIN_CONFIG = """\
# Test config — minimal but valid.
provider:
  name: anthropic
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY
  base_url: null
  timeout_seconds: 120
  retry:
    max_attempts: 3
    backoff_base_seconds: 1.0
    backoff_max_seconds: 10.0
  params: {}

runtime:
  workspace: /tmp
  max_iterations: 10
  max_tool_calls_per_turn: 25
  show_thinking: false
  log_level: info
  system_prompt: ""
  iteration_cap_message: ""
  tool_call_cap_message: ""
  cycle_detection_threshold: 3
  cycle_detected_message: ""

tools:
  enabled: [ls]
  config: {}

plugins:
  failure_threshold: 3
  exception_message_max_chars: 500
  # Initial state: only one built-in is listed
  enabled:
    - name: guard
      enabled: true
      config: {}
      hooks_order:
        before_tool_call: 10

tui:
  enabled: false
  theme: dark
  inline_mode: true
  spinner_style: dots
  prompt_prefix: "❯ "
  show_token_counts: true
  show_event_count: false

bootstrap:
  create_workspace_dir: false
  write_example_session: false
"""


@pytest.fixture
def config_path(tmp_path) -> Path:
    p = tmp_path / "config.yml"
    p.write_text(_MIN_CONFIG)
    return p


# ── write_plugin_enablement ────────────────────────────────────────────────


def test_write_plugin_enablement_appends_new_entry(config_path):
    changes = write_plugin_enablement(
        config_path, name="briefbot", enabled=True,
    )
    assert len(changes) == 1
    text = config_path.read_text()
    assert "name: briefbot" in text
    assert "guard" in text  # existing entry preserved
    assert "# Initial state" in text  # comment preserved


def test_write_plugin_enablement_updates_existing(config_path):
    write_plugin_enablement(config_path, name="briefbot", enabled=True)
    write_plugin_enablement(config_path, name="briefbot", enabled=False)
    text = config_path.read_text()
    # Two writes, not two entries
    assert text.count("name: briefbot") == 1
    assert "enabled: false" in text.split("name: briefbot")[1].split("name:")[0]


def test_write_plugin_enablement_preserves_user_config(config_path):
    # Append a plugin with a config block
    write_plugin_enablement(
        config_path, name="websearch", enabled=True,
        config={"backend": "brave"},
    )
    # User edits the config (we simulate that by re-reading and verifying)
    text = config_path.read_text()
    assert "backend: brave" in text
    # Now toggle enabled — the config block must remain
    write_plugin_enablement(config_path, name="websearch", enabled=False)
    text = config_path.read_text()
    assert "backend: brave" in text


def test_remove_plugin_entry(config_path):
    write_plugin_enablement(config_path, name="briefbot", enabled=True)
    assert "name: briefbot" in config_path.read_text()
    changes = remove_plugin_entry(config_path, name="briefbot")
    assert len(changes) == 1
    assert "name: briefbot" not in config_path.read_text()
    # Removing a non-existent plugin is a no-op (not an error)
    assert remove_plugin_entry(config_path, name="briefbot") == []


# ── find_new_plugins ──────────────────────────────────────────────────────


def _fake_report(*names: str) -> DiscoveryReport:
    discovered = [
        DiscoveredPlugin(
            name=n, builder=lambda _c, _x: object(),
            package=f"arc-plugin-{n}", package_version="1.0.0",
            entry_point_value=f"{n}.plugin:build",
        )
        for n in names
    ]
    return DiscoveryReport(discovered=discovered)


def _cfg_with(*names: str) -> PluginsConfig:
    return PluginsConfig(
        failure_threshold=3, exception_message_max_chars=500,
        enabled=[
            PluginEntry(name=n, enabled=True, config={}, hooks_order={})
            for n in names
        ],
    )


def test_find_new_plugins_returns_unseen():
    report = _fake_report("alpha", "beta", "gamma")
    cfg = _cfg_with("beta")
    new = find_new_plugins(report, cfg)
    assert {d.name for d in new} == {"alpha", "gamma"}


def test_find_new_plugins_empty_when_all_known():
    report = _fake_report("alpha", "beta")
    cfg = _cfg_with("alpha", "beta")
    assert find_new_plugins(report, cfg) == []


def test_find_new_plugins_respects_disabled_entries():
    """Plugin already in config (even if disabled) should not be re-prompted."""
    report = _fake_report("alpha")
    cfg = PluginsConfig(
        failure_threshold=3, exception_message_max_chars=500,
        enabled=[PluginEntry(name="alpha", enabled=False, config={}, hooks_order={})],
    )
    assert find_new_plugins(report, cfg) == []


# ── run_first_run_flow ────────────────────────────────────────────────────


def test_run_first_run_flow_persists_yes(config_path):
    report = _fake_report("briefbot")
    cfg = load(config_path)
    new = find_new_plugins(report, cfg.plugins)

    emitted = []
    outcomes = run_first_run_flow(
        config_path,
        new_plugins=new,
        interactive=True,
        prompt_fn=lambda _q: True,
        emit=lambda t, p: emitted.append((t, p)),
    )
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.name == "briefbot"
    assert o.enabled is True
    assert o.persisted is True
    assert "name: briefbot" in config_path.read_text()
    # Reload and confirm enabled=true
    reloaded = load(config_path)
    assert any(e.name == "briefbot" and e.enabled for e in reloaded.plugins.enabled)
    assert any(t == "plugin.first_run.enabled" for t, _ in emitted)


def test_run_first_run_flow_persists_no(config_path):
    report = _fake_report("briefbot")
    cfg = load(config_path)
    new = find_new_plugins(report, cfg.plugins)

    outcomes = run_first_run_flow(
        config_path, new_plugins=new,
        interactive=True, prompt_fn=lambda _q: False,
    )
    o = outcomes[0]
    assert o.enabled is False
    assert o.persisted is True
    reloaded = load(config_path)
    # Entry written with enabled=false so we don't re-prompt next time
    assert any(e.name == "briefbot" and not e.enabled for e in reloaded.plugins.enabled)


def test_run_first_run_flow_skips_in_headless(config_path):
    report = _fake_report("briefbot")
    cfg = load(config_path)
    new = find_new_plugins(report, cfg.plugins)

    outcomes = run_first_run_flow(
        config_path, new_plugins=new,
        interactive=False,
    )
    o = outcomes[0]
    assert o.skipped_reason is not None
    assert o.persisted is False
    # Config untouched
    assert "name: briefbot" not in config_path.read_text()


def test_run_first_run_flow_empty_new_plugins_is_noop(config_path, tmp_path):
    """No new plugins → no prompt, no write, empty outcomes."""
    outcomes = run_first_run_flow(
        config_path, new_plugins=[], interactive=True,
        prompt_fn=lambda _q: pytest.fail("should not prompt"),
    )
    assert outcomes == []


# ── plugin menu rows ──────────────────────────────────────────────────────


def test_collect_rows_marks_dangling_entries(config_path, monkeypatch):
    """A plugin listed in config.yml but not in discovery + not built-in =
    dangling. Menu should surface it for cleanup.
    """
    write_plugin_enablement(config_path, name="ghost", enabled=True)
    # Force discovery to return nothing extra
    from arc.plugins import _refresh_builders
    monkeypatch.setattr(
        "arc.plugins.discovery.entry_points",
        lambda: type("E", (), {"select": lambda self, *, group: []})(),
    )
    monkeypatch.setattr("arc.plugins.discovery.distributions", lambda: [])
    _refresh_builders()

    from arc.setup.plugin_menu import collect_rows
    rows = collect_rows(config_path)
    by_name = {r.name: r for r in rows}
    assert "ghost" in by_name
    assert by_name["ghost"].kind == "dangling"
    assert "guard" in by_name
    assert by_name["guard"].kind == "builtin"


def test_collect_rows_renders_status_marker(config_path, monkeypatch):
    write_plugin_enablement(config_path, name="ghost", enabled=True)
    from arc.plugins import _refresh_builders
    monkeypatch.setattr(
        "arc.plugins.discovery.entry_points",
        lambda: type("E", (), {"select": lambda self, *, group: []})(),
    )
    monkeypatch.setattr("arc.plugins.discovery.distributions", lambda: [])
    _refresh_builders()

    from arc.setup.plugin_menu import collect_rows
    rows = collect_rows(config_path)
    ghost = next(r for r in rows if r.name == "ghost")
    assert "[!]" in ghost.display


def test_list_plugins_prints_table(config_path, capsys):
    from arc.setup.plugin_menu import list_plugins
    list_plugins(config_path)
    out = capsys.readouterr().out
    assert "guard" in out
    # Built-in tag appears (any of the format variants)
    assert "built-in" in out
