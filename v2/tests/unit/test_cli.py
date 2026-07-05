"""Tests for the arc CLI.

These mostly exercise the argument-parsing and subcommand dispatch paths.
The `arc run` subcommand requires a provider, so we monkey-patch the
provider builder to return a fake.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from arc.cli import main


def _set_home(monkeypatch, path: Path) -> None:
    monkeypatch.setenv("ARC_HOME", str(path))


# ── bootstrap ──────────────────────────────────────────────────────────────


def test_bootstrap_creates_home_and_prints(tmp_path, capsys, monkeypatch):
    home = tmp_path / "h"
    _set_home(monkeypatch, home)
    rc = main(["bootstrap"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "created home directory" in captured.out
    assert "wrote config.yml" in captured.out
    assert (home / "config.yml").exists()


def test_bootstrap_idempotent(tmp_path, capsys, monkeypatch):
    home = tmp_path / "h"
    _set_home(monkeypatch, home)
    main(["bootstrap"])
    capsys.readouterr()  # drain
    rc = main(["bootstrap"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "no changes" in captured.out


def test_bootstrap_force_rewrites_config(tmp_path, capsys, monkeypatch):
    home = tmp_path / "h"
    _set_home(monkeypatch, home)
    main(["bootstrap"])
    (home / "config.yml").write_text("# user edited\n")
    capsys.readouterr()

    rc = main(["bootstrap", "--force"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "wrote config.yml" in captured.out
    assert "provider:" in (home / "config.yml").read_text()


def test_home_flag_overrides_env(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("ARC_HOME", "/should/not/be/used")
    home = tmp_path / "explicit"
    rc = main(["--home", str(home), "bootstrap"])
    assert rc == 0
    assert (home / "config.yml").exists()


# ── config ────────────────────────────────────────────────────────────────


def test_config_path_after_bootstrap(tmp_path, capsys, monkeypatch):
    home = tmp_path / "h"
    _set_home(monkeypatch, home)
    main(["bootstrap"])
    capsys.readouterr()
    rc = main(["config", "path"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "config.yml" in captured.out.strip()


def test_config_path_missing_returns_nonzero(tmp_path, capsys, monkeypatch):
    home = tmp_path / "empty"
    _set_home(monkeypatch, home)
    rc = main(["config", "path"])
    assert rc == 1


def test_config_show_prints_yaml(tmp_path, capsys, monkeypatch):
    home = tmp_path / "h"
    _set_home(monkeypatch, home)
    main(["bootstrap"])
    capsys.readouterr()
    rc = main(["config", "show"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "provider:" in captured.out
    assert "gemini" in captured.out


def test_config_show_missing_returns_error(tmp_path, capsys, monkeypatch):
    home = tmp_path / "nope"
    _set_home(monkeypatch, home)
    rc = main(["config", "show"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no config" in captured.err
    assert "arc bootstrap" in captured.err


# ── sessions / show ───────────────────────────────────────────────────────


def test_sessions_empty_prints_message(tmp_path, capsys, monkeypatch):
    home = tmp_path / "h"
    _set_home(monkeypatch, home)
    main(["bootstrap"])
    capsys.readouterr()
    rc = main(["sessions"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "no sessions" in captured.err


def test_sessions_lists_recorded(tmp_path, capsys, monkeypatch):
    home = tmp_path / "h"
    _set_home(monkeypatch, home)
    main(["bootstrap"])
    capsys.readouterr()
    # Inject a fake index entry
    idx = home / "sessions" / "index.jsonl"
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.write_text(json.dumps({
        "session_id": "Ses_fake",
        "started_at": "2026-05-17T09:00:00.000000+00:00",
        "ended_at": "2026-05-17T09:00:01.000000+00:00",
        "provider": "gemini",
        "model": "test-model",
    }) + "\n")

    rc = main(["sessions"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Ses_fake" in captured.out
    assert "gemini" in captured.out


def test_show_missing_session_returns_error(tmp_path, capsys, monkeypatch):
    home = tmp_path / "h"
    _set_home(monkeypatch, home)
    main(["bootstrap"])
    capsys.readouterr()
    rc = main(["show", "Ses_missing"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no events" in captured.err


def test_show_renders_events(tmp_path, capsys, monkeypatch):
    home = tmp_path / "h"
    _set_home(monkeypatch, home)
    main(["bootstrap"])
    capsys.readouterr()

    sd = home / "sessions" / "Ses_demo"
    sd.mkdir(parents=True)
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "turn.started", "ts": "2026-05-17T09:00:00.123456",
                    "stage": "AgentSession", "scope": "main"}) + "\n"
        + json.dumps({"type": "turn.ended", "ts": "2026-05-17T09:00:01.000000",
                      "stage": "AgentSession", "scope": "main"}) + "\n"
    )
    rc = main(["show", "Ses_demo"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "turn.started" in captured.out
    assert "turn.ended" in captured.out


# ── run (one-shot) — uses a fake provider ─────────────────────────────────


def test_run_one_shot_with_fake_provider(tmp_path, capsys, monkeypatch):
    home = tmp_path / "h"
    _set_home(monkeypatch, home)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-for-bootstrap")

    # Replace the provider builder so we don't need a real Gemini call
    from arc.providers.base import LLMProvider
    from arc.runtime.hooks import ContentBlock, LLMResponse

    class FakeProv:
        name = "fake"
        def __init__(self):
            self.calls = []
        def chat(self, req):
            self.calls.append(req)
            return LLMResponse(
                content=[ContentBlock(type="text", text="hello from fake")],
                stop_reason="end_turn", input_tokens=2, output_tokens=3, raw={},
            )

    import arc.providers as providers_mod
    monkeypatch.setattr(providers_mod, "build", lambda cfg: FakeProv())

    rc = main(["run", "say hi"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "hello from fake" in captured.out

    # A session was recorded
    sessions_dir = home / "sessions"
    session_dirs = [p for p in sessions_dir.iterdir() if p.is_dir()]
    assert len(session_dirs) == 1
    assert (session_dirs[0] / "events.jsonl").is_file()


# ── interactive (placeholder) ─────────────────────────────────────────────


def test_bare_arc_launches_tui(tmp_path, monkeypatch):
    """`arc` with no subcommand should launch the TUI. We verify it constructs
    rather than running the prompt loop (which would need a real TTY)."""
    home = tmp_path / "h"
    _set_home(monkeypatch, home)
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    # Patch the provider builder + TUIApp.run so we don't touch network or TTY
    import arc.providers as providers_mod
    from arc.runtime.hooks import ContentBlock, LLMResponse

    class FakeProv:
        name = "fake"
        def chat(self, req):
            return LLMResponse(content=[ContentBlock(type="text", text="ok")],
                               stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={})

    monkeypatch.setattr(providers_mod, "build", lambda cfg: FakeProv())

    # Replace TUIApp.run with an immediate-return stub to skip prompt loop
    import arc.tui.app as tui_mod
    monkeypatch.setattr(tui_mod.TUIApp, "run", lambda self: 0)

    rc = main([])
    assert rc == 0


# ── replay / resume / rerun CLI smoke tests (guard-rails for the cli refactor) ──


def _fake_provider(*_a, **_k):
    from arc.runtime.hooks import ContentBlock, LLMResponse

    class _FP:
        name = "fake"
        def chat(self, req):
            return LLMResponse(
                content=[ContentBlock(type="text", text="fake reply")],
                stop_reason="end_turn", input_tokens=1, output_tokens=1, raw={})
    return _FP()


def _record_session(monkeypatch, home: Path) -> str:
    """Run one fake turn to produce a recorded session; return its id."""
    _set_home(monkeypatch, home)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-for-bootstrap")
    import arc.providers as pm
    monkeypatch.setattr(pm, "build", _fake_provider)
    assert main(["run", "hello there"]) == 0
    dirs = [p for p in (home / "sessions").iterdir() if p.is_dir()]
    assert len(dirs) == 1
    return dirs[0].name


def test_replay_cli_smoke(tmp_path, monkeypatch):
    # Guard the replay session-wiring: it must construct + run without raising.
    # (A synthetic session with empty .raw diverges, which is fine — we only
    # assert the wiring doesn't crash, which is what the refactor could break.
    # Also locks in the fix for the subagent-tool double-registration crash.)
    home = tmp_path / "h"
    sid = _record_session(monkeypatch, home)
    rc = main(["replay", sid])
    assert isinstance(rc, int)


def test_resume_restore_only_cli_smoke(tmp_path, monkeypatch):
    home = tmp_path / "h"
    sid = _record_session(monkeypatch, home)
    # --no-tui + no --prompt: restore and exit (no interactive loop)
    assert main(["resume", sid, "--no-tui"]) == 0


def test_rerun_cli_smoke(tmp_path, monkeypatch):
    home = tmp_path / "h"
    sid = _record_session(monkeypatch, home)
    assert main(["rerun", sid]) == 0
