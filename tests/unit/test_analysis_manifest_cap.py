"""Unit tests for the 0090b analysis-manifest size cap.

session_paths.build_analysis_manifest() embeds a list of paged artifacts into
the agent's system prompt. Without bounds, long-running sessions accumulate
hundreds of artifacts and silently grow the system prompt past safe limits.
0090b enforces both a count cap and a char cap.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def isolated_arc_home(tmp_path, monkeypatch):
    """Point ARC_HOME at a per-test tmp_path so manifests scan only our fixtures."""
    monkeypatch.setenv("ARC_HOME", str(tmp_path))
    import app_config
    monkeypatch.setattr(app_config.settings, "arc_home", str(tmp_path), raising=False)
    yield tmp_path


def _write_n_artifacts(home: Path, n: int) -> None:
    analysis = home / "analysis" / "test_binary"
    analysis.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (analysis / f"artifact_{i:03d}.txt").write_text(f"content {i}")


def test_manifest_empty_when_no_artifacts(isolated_arc_home):
    from session_paths import build_analysis_manifest
    assert build_analysis_manifest() == ""


def test_manifest_respects_count_cap(isolated_arc_home):
    from session_paths import build_analysis_manifest
    _write_n_artifacts(isolated_arc_home, 50)  # 50 > default cap of 20
    out = build_analysis_manifest(max_entries=20, max_chars=10000)
    # Count the artifact lines (each line starts with "  _analysis/")
    lines = [l for l in out.splitlines() if l.startswith("  _analysis/")]
    assert len(lines) <= 20
    assert "more)" in out  # truncation note present


def test_manifest_respects_char_cap(isolated_arc_home):
    """Char cap kicks in even when the count cap would let everything through."""
    from session_paths import build_analysis_manifest
    _write_n_artifacts(isolated_arc_home, 15)  # 15 < default count cap
    out = build_analysis_manifest(max_entries=100, max_chars=300)
    assert len(out) <= 400  # 300 budget + small slack for header/footer
    assert "more)" in out  # something was truncated


def test_manifest_below_caps_includes_everything(isolated_arc_home):
    """Both caps comfortably accommodate the artifacts → no truncation note."""
    from session_paths import build_analysis_manifest
    _write_n_artifacts(isolated_arc_home, 3)
    out = build_analysis_manifest(max_entries=20, max_chars=10000)
    assert "more)" not in out
    lines = [l for l in out.splitlines() if l.startswith("  _analysis/")]
    assert len(lines) == 3
