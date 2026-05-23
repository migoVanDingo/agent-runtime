"""Unit tests for `arc.wipe`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from arc.bootstrap import bootstrap, paths_for
from arc.wipe import (
    WipeTargets,
    build_plan,
    execute_plan,
    format_plan,
)


def _populate(home: Path) -> None:
    """Bootstrap and seed a session + a pid file so wipe has something to do."""
    bootstrap(home)
    p = paths_for(home)
    # Fake session
    sess_dir = p.sessions_dir / "TESTSESS01"
    sess_dir.mkdir()
    (sess_dir / "events.jsonl").write_text('{"type": "session.started"}\n')
    (sess_dir / "session.log").write_text("ok\n")
    # Fake llm pid + log
    (p.llm_dir / "current.pid").write_text(json.dumps({
        "pid": 1, "model_id": "fake", "started_at": "2026-01-01T00:00:00+00:00",
    }))
    (p.llm_dir / "current.log").write_text("server stderr…\n")
    # Fake history + pricing cache
    (home / "history").write_text("> ls\n> arc llm list\n")
    (home / "pricing_cache.json").write_text('{"data": {}}')


# ── WipeTargets ────────────────────────────────────────────────────────────


def test_targets_empty_detection():
    assert WipeTargets().is_empty is True
    assert WipeTargets(sessions=True).is_empty is False


def test_targets_default_to_sessions_when_empty():
    out = WipeTargets().with_default_if_empty()
    assert out.sessions is True
    assert out.all_ is False


def test_targets_with_flags_unchanged():
    inp = WipeTargets(llm=True, history=True)
    assert inp.with_default_if_empty() == inp


# ── build_plan ─────────────────────────────────────────────────────────────


def test_plan_sessions_only(tmp_path: Path):
    _populate(tmp_path)
    plan = build_plan(tmp_path, WipeTargets(sessions=True))
    assert len(plan.paths_to_remove) == 1
    assert plan.paths_to_remove[0] == paths_for(tmp_path).sessions_dir


def test_plan_llm_flags_pid_warning(tmp_path: Path):
    _populate(tmp_path)
    plan = build_plan(tmp_path, WipeTargets(llm=True))
    assert plan.pid_file_present is True


def test_plan_history_and_pricing_cache(tmp_path: Path):
    _populate(tmp_path)
    plan = build_plan(tmp_path, WipeTargets(history=True, pricing_cache=True))
    names = {p.name for p in plan.paths_to_remove}
    assert names == {"history", "pricing_cache.json"}


def test_plan_skips_missing_files(tmp_path: Path):
    bootstrap(tmp_path)
    # No history file written
    plan = build_plan(tmp_path, WipeTargets(history=True))
    assert plan.is_noop


def test_plan_all_takes_priority_over_others(tmp_path: Path):
    _populate(tmp_path)
    plan = build_plan(tmp_path, WipeTargets(all_=True, sessions=True, llm=True))
    # Only the ARC_HOME dir itself, not each subtree separately
    assert len(plan.paths_to_remove) == 1
    assert plan.paths_to_remove[0] == tmp_path


def test_plan_all_surfaces_pid_warning(tmp_path: Path):
    _populate(tmp_path)
    plan = build_plan(tmp_path, WipeTargets(all_=True))
    assert plan.pid_file_present is True


def test_plan_all_on_missing_home_is_noop(tmp_path: Path):
    nonexistent = tmp_path / "never_bootstrapped"
    plan = build_plan(nonexistent, WipeTargets(all_=True))
    assert plan.is_noop


def test_total_size_bytes_nonzero(tmp_path: Path):
    _populate(tmp_path)
    plan = build_plan(tmp_path, WipeTargets(all_=True))
    assert plan.total_size_bytes() > 0


# ── execute_plan ───────────────────────────────────────────────────────────


def test_execute_removes_sessions(tmp_path: Path):
    _populate(tmp_path)
    p = paths_for(tmp_path)
    assert p.sessions_dir.is_dir()
    plan = build_plan(tmp_path, WipeTargets(sessions=True))
    removed = execute_plan(plan)
    assert len(removed) == 1
    assert not p.sessions_dir.exists()
    # config.yml/catalog.yml/llm_servers.yml untouched
    assert p.config_file.is_file()
    assert p.catalog_file.is_file()
    assert p.llm_servers_file.is_file()


def test_execute_all_nukes_home(tmp_path: Path):
    _populate(tmp_path)
    assert tmp_path.is_dir()
    plan = build_plan(tmp_path, WipeTargets(all_=True))
    execute_plan(plan)
    assert not tmp_path.exists()


def test_execute_is_idempotent(tmp_path: Path):
    _populate(tmp_path)
    plan = build_plan(tmp_path, WipeTargets(sessions=True))
    execute_plan(plan)
    # Running again should be a no-op (no files left)
    plan2 = build_plan(tmp_path, WipeTargets(sessions=True))
    assert plan2.is_noop


def test_execute_refuses_paths_outside_home(tmp_path: Path, monkeypatch):
    """Belt-and-suspenders: a hand-crafted plan with a sibling path
    shouldn't escape ARC_HOME."""
    _populate(tmp_path)
    sibling = tmp_path.parent / "should_survive"
    sibling.mkdir(exist_ok=True)
    (sibling / "important.txt").write_text("don't lose me")

    # Hand-build a plan with the sibling path injected
    plan = build_plan(tmp_path, WipeTargets(sessions=True))
    plan.paths_to_remove.append(sibling)

    execute_plan(plan)

    assert sibling.exists()
    assert (sibling / "important.txt").read_text() == "don't lose me"


# ── format_plan ────────────────────────────────────────────────────────────


def test_format_plan_renders_relative_paths(tmp_path: Path):
    _populate(tmp_path)
    plan = build_plan(tmp_path, WipeTargets(sessions=True, history=True))
    text = format_plan(plan)
    assert "sessions/" in text
    assert "history" in text
    assert "total" in text


def test_format_plan_warns_about_pid_file(tmp_path: Path):
    _populate(tmp_path)
    plan = build_plan(tmp_path, WipeTargets(llm=True))
    text = format_plan(plan)
    assert "PID file is present" in text
    assert "arc llm stop" in text


def test_format_plan_empty(tmp_path: Path):
    plan = build_plan(tmp_path, WipeTargets(history=True))  # no history file exists
    text = format_plan(plan)
    assert "nothing to wipe" in text
