"""Unit tests for the batch scheduler (0019)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from arc.replay.batch import (
    BatchResult,
    BatchTarget,
    _session_id_from_stdout,
    make_plan,
    run_batch,
)


# ── make_plan ──────────────────────────────────────────────────────────────


def test_make_plan_buckets_cloud_and_ollama_into_parallel():
    targets = [
        BatchTarget(provider="anthropic", model="x"),
        BatchTarget(provider="ollama", model="y"),
        BatchTarget(provider="gemini", model="z"),
    ]
    plan = make_plan(targets)
    assert plan.parallel == targets
    assert plan.serial == []


def test_make_plan_buckets_llama_cpp_serial():
    targets = [
        BatchTarget(provider="anthropic", model="x"),
        BatchTarget(provider="llama_cpp", model="qwen"),
        BatchTarget(provider="llama_cpp", model="llama"),
    ]
    plan = make_plan(targets)
    assert len(plan.parallel) == 1
    assert plan.parallel[0].provider == "anthropic"
    assert [t.provider for t in plan.serial] == ["llama_cpp", "llama_cpp"]


# ── session-id extraction from stdout ─────────────────────────────────────


def test_session_id_extraction_finds_the_replay_id():
    text = "some preamble\nreplaying 01ABC → 01XYZ  (mode 3 (live LLM))\ntrailing line\n"
    assert _session_id_from_stdout(text) == "01XYZ"


def test_session_id_extraction_returns_none_when_absent():
    assert _session_id_from_stdout("nothing relevant here") is None


def test_session_id_extraction_handles_unicode_arrow():
    text = "replaying 01ABC → 01XYZ (mode 2)"
    assert _session_id_from_stdout(text) == "01XYZ"


# ── run_batch ─────────────────────────────────────────────────────────────


def _stub_popen(stdout: bytes, returncode: int = 0):
    """Return a MagicMock Popen-like object."""
    proc = MagicMock()
    proc.communicate = MagicMock(return_value=(stdout, b""))
    proc.returncode = returncode
    proc.pid = 12345
    return proc


def _stub_run_serial(stdout: str, returncode: int = 0):
    """Return a MagicMock CompletedProcess-like object."""
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = ""
    proc.returncode = returncode
    return proc


def test_run_batch_calls_arc_replay_per_target(tmp_path: Path):
    targets = [
        BatchTarget(provider="anthropic", model="claude-haiku-4-5"),
        BatchTarget(provider="ollama", model="llama3.1:8b"),
    ]
    spawned_argvs: list[list[str]] = []

    def _spy(argv, stdout=None, stderr=None, env=None):
        spawned_argvs.append(argv)
        return _stub_popen(b"replaying 01SRC \xe2\x86\x92 01TGT  (mode 3)\n")

    with patch("subprocess.Popen", side_effect=_spy):
        results = run_batch(
            source_session_id="01SRC",
            targets=targets,
            arc_home=tmp_path,
            max_cost_usd=5.0,
        )

    # Two children spawned, both parallel (no llama_cpp targets)
    assert len(spawned_argvs) == 2
    for argv in spawned_argvs:
        assert "replay" in argv
        assert "01SRC" in argv
        assert "--live-llm" in argv
        assert "--override-provider" in argv
        assert "--override-model" in argv
        assert "--max-cost-usd" in argv
        # --home was passed through
        assert "--home" in argv
    assert all(r.target_session_id == "01TGT" for r in results)
    assert all(r.succeeded for r in results)


def test_run_batch_serializes_llama_cpp_targets(tmp_path: Path):
    targets = [
        BatchTarget(provider="anthropic", model="x"),
        BatchTarget(provider="llama_cpp", model="qwen"),
        BatchTarget(provider="llama_cpp", model="llama"),
    ]
    parallel_seen = []
    serial_seen: list[list[str]] = []

    def _spy_popen(argv, stdout=None, stderr=None, env=None):
        parallel_seen.append(argv)
        return _stub_popen(b"replaying 01SRC \xe2\x86\x92 01PAR  (mode 3)\n")

    def _spy_run(argv, capture_output=True, text=True, env=None):
        serial_seen.append(argv)
        return _stub_run_serial("replaying 01SRC → 01SER  (mode 3)\n")

    with patch("subprocess.Popen", side_effect=_spy_popen), \
         patch("subprocess.run", side_effect=_spy_run):
        results = run_batch(
            source_session_id="01SRC",
            targets=targets,
            arc_home=tmp_path,
            max_cost_usd=None,
        )

    assert len(parallel_seen) == 1
    assert len(serial_seen) == 2
    # All three results returned
    assert len(results) == 3


def test_run_batch_callbacks_fire(tmp_path: Path):
    targets = [BatchTarget(provider="anthropic", model="x")]
    started: list[BatchTarget] = []
    done: list[BatchResult] = []
    with patch("subprocess.Popen", side_effect=lambda *a, **kw: _stub_popen(
        b"replaying 01SRC \xe2\x86\x92 01TGT  (mode 3)\n",
    )):
        run_batch(
            source_session_id="01SRC",
            targets=targets,
            arc_home=tmp_path,
            max_cost_usd=None,
            on_target_start=started.append,
            on_target_done=done.append,
        )
    assert len(started) == 1
    assert len(done) == 1
    assert done[0].succeeded


def test_run_batch_records_failure(tmp_path: Path):
    targets = [BatchTarget(provider="anthropic", model="x")]
    with patch("subprocess.Popen", side_effect=lambda *a, **kw: _stub_popen(
        b"replaying 01SRC \xe2\x86\x92 01TGT\n", returncode=1,
    )):
        results = run_batch(
            source_session_id="01SRC",
            targets=targets,
            arc_home=tmp_path,
            max_cost_usd=None,
        )
    assert not results[0].succeeded
    assert results[0].return_code == 1
