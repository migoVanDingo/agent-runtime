"""Tests for Phase G sandbox hardening: auto backend resolution, fail-fast."""
import pytest
from unittest.mock import patch, MagicMock
from runtime.sandbox.manager import SandboxManager, _is_docker_infrastructure_failure
from runtime.sandbox.base import SandboxCommandResult


def _cfg(backend, allow_host=True):
    class Cfg:
        pass
    c = Cfg()
    c.backend = backend
    c.allow_host_backend = allow_host
    c.docker_image = "python:3.11-slim"
    c.default_network = "disabled"
    c.command_timeout_seconds = 5
    c.max_output_chars = 1000
    c.workspace_root = "."
    c.allowed_read_roots = []
    c.allowed_write_roots = []
    return c


def _fake_host_result():
    return SandboxCommandResult(
        stdout="hello", stderr="", exit_code=0,
        timed_out=False, duration_ms=5,
        sandbox_backend="host", isolation="none",
    )


# ── backend: docker fail-fast ─────────────────────────────────────────────────

def test_docker_backend_fails_fast_on_infra_failure():
    """backend: docker must raise, never fall back."""
    mgr = SandboxManager(_cfg("docker", allow_host=True))
    docker_infra_result = SandboxCommandResult(
        stdout="Is the docker daemon running?", stderr="",
        exit_code=1, timed_out=False, duration_ms=5,
        sandbox_backend="docker", isolation="container",
    )
    with patch("runtime.sandbox.manager.DockerShellBackend") as mock_cls:
        mock_cls.return_value.run.return_value = docker_infra_result
        with pytest.raises(RuntimeError, match="docker backend failed"):
            mgr._dispatch("docker", MagicMock())


# ── backend: auto resolution ──────────────────────────────────────────────────

def test_auto_uses_host_when_no_docker_no_macos():
    """auto with no docker and non-macOS → host fallback."""
    mgr = SandboxManager(_cfg("auto"))
    with patch("shutil.which", return_value=None), \
         patch("platform.system", return_value="Linux"), \
         patch("runtime.sandbox.manager.HostShellBackend") as mock_host:
        mock_host.return_value.run.return_value = _fake_host_result()
        result = mgr._run_auto(MagicMock())
    assert result.sandbox_backend == "host"
    assert "sandbox warning" in result.stdout


def test_auto_prefers_docker_when_available():
    """auto with docker available → uses docker."""
    mgr = SandboxManager(_cfg("auto"))
    docker_result = SandboxCommandResult(
        stdout="ok", stderr="", exit_code=0, timed_out=False,
        duration_ms=100, sandbox_backend="docker", isolation="container",
    )
    with patch("shutil.which", return_value="/usr/bin/docker"), \
         patch("runtime.sandbox.manager.DockerShellBackend") as mock_cls:
        mock_cls.return_value.run.return_value = docker_result
        result = mgr._run_auto(MagicMock())
    assert result.sandbox_backend == "docker"


# ── _is_docker_infrastructure_failure ────────────────────────────────────────

def test_docker_infra_failure_detected():
    result = SandboxCommandResult(
        stdout="Is the docker daemon running?", stderr="",
        exit_code=1, timed_out=False, duration_ms=5,
        sandbox_backend="docker", isolation="container",
    )
    assert _is_docker_infrastructure_failure(result) is True


def test_docker_success_not_infra_failure():
    result = SandboxCommandResult(
        stdout="hello", stderr="", exit_code=0, timed_out=False,
        duration_ms=5, sandbox_backend="docker", isolation="container",
    )
    assert _is_docker_infrastructure_failure(result) is False


def test_host_backend_not_infra_failure():
    result = SandboxCommandResult(
        stdout="error msg", stderr="", exit_code=1, timed_out=False,
        duration_ms=5, sandbox_backend="host", isolation="none",
    )
    assert _is_docker_infrastructure_failure(result) is False
