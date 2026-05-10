"""Target adapters — pluggable per artifact type."""
from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class TargetSpec:
    type: Literal[
        "native_binary",  # pre-compiled binary for the host platform
        "c_source",       # C source file — compile with gcc inside container
        "cpp_source",     # C++ source file — compile with g++ inside container
        "python_source",  # Python script — run with python3 inside container
        "jar",            # JAR file — run with java -jar inside container
        # Future: "elf_binary", "apk", "server"
    ]
    path: str                              # workspace-relative or absolute
    build_flags: list[str] = field(default_factory=list)
    image: str | None = None              # override default image for this type


@dataclass
class TestCase:
    id: str
    args: list[str] = field(default_factory=list)
    stdin: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 10.0


@dataclass
class InvocationResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int


class TargetAdapter:
    """Base adapter. Subclasses implement build and invoke for a target type."""

    default_image: str = "ubuntu:22.04"
    runs_locally: bool = False  # True = host execution, False = container

    def image_for(self, spec: TargetSpec) -> str:
        return spec.image or self.default_image

    def build_commands(self, spec: TargetSpec, artifact_path: str) -> str:
        """Shell commands to prepare the artifact inside the container.
        artifact_path is where the runnable result should end up."""
        return f"cp {shlex.quote(spec.path)} {shlex.quote(artifact_path)}"

    def invoke_command(self, artifact_path: str, case: TestCase) -> str:
        """Shell command to run one test case. stdout/stderr captured by framework."""
        args = " ".join(shlex.quote(a) for a in case.args)
        return f"{shlex.quote(artifact_path)} {args}"

    def run_locally(self, spec: TargetSpec, case: TestCase) -> InvocationResult:
        """Execute on the host. Used for native_binary (host-platform binaries)."""
        start = time.monotonic()
        try:
            cmd = [str(Path(spec.path).resolve())] + case.args
            stdin_bytes = case.stdin.encode() if case.stdin else None
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=case.timeout_seconds,
                input=stdin_bytes,
            )
            return InvocationResult(
                stdout=result.stdout.decode("utf-8", errors="replace"),
                stderr=result.stderr.decode("utf-8", errors="replace"),
                exit_code=result.returncode,
                timed_out=False,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except subprocess.TimeoutExpired:
            return InvocationResult(
                stdout="", stderr="",
                exit_code=None, timed_out=True,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:
            return InvocationResult(
                stdout="", stderr=str(e),
                exit_code=1, timed_out=False,
                duration_ms=int((time.monotonic() - start) * 1000),
            )


class NativeBinaryAdapter(TargetAdapter):
    """Host-platform binary. Runs locally since Docker uses Linux and the binary
    may be Mach-O (macOS). Compilation targets are handled separately below."""

    default_image = "ubuntu:22.04"
    runs_locally = True


class CSourceAdapter(TargetAdapter):
    """C source file. Compiled inside the container with gcc."""

    default_image = "gcc:12"
    runs_locally = False

    def build_commands(self, spec: TargetSpec, artifact_path: str) -> str:
        flags = " ".join(spec.build_flags) if spec.build_flags else ""
        src = shlex.quote(spec.path)
        out = shlex.quote(artifact_path)
        return f"gcc {flags} -o {out} {src}"

    def invoke_command(self, artifact_path: str, case: TestCase) -> str:
        args = " ".join(shlex.quote(a) for a in case.args)
        return f"{shlex.quote(artifact_path)} {args}"


class CppSourceAdapter(TargetAdapter):
    """C++ source file. Compiled inside the container with g++."""

    default_image = "gcc:12"
    runs_locally = False

    def build_commands(self, spec: TargetSpec, artifact_path: str) -> str:
        flags = " ".join(spec.build_flags) if spec.build_flags else ""
        src = shlex.quote(spec.path)
        out = shlex.quote(artifact_path)
        return f"g++ {flags} -o {out} {src}"

    def invoke_command(self, artifact_path: str, case: TestCase) -> str:
        args = " ".join(shlex.quote(a) for a in case.args)
        return f"{shlex.quote(artifact_path)} {args}"


class PythonSourceAdapter(TargetAdapter):
    """Python script. No compilation step."""

    default_image = "python:3.11-slim"
    runs_locally = False

    def build_commands(self, spec: TargetSpec, artifact_path: str) -> str:
        return f"cp {shlex.quote(spec.path)} {shlex.quote(artifact_path)}.py"

    def invoke_command(self, artifact_path: str, case: TestCase) -> str:
        args = " ".join(shlex.quote(a) for a in case.args)
        return f"python3 {shlex.quote(artifact_path)}.py {args}"


ADAPTER_REGISTRY: dict[str, TargetAdapter] = {
    "native_binary": NativeBinaryAdapter(),
    "c_source":      CSourceAdapter(),
    "cpp_source":    CppSourceAdapter(),
    "python_source": PythonSourceAdapter(),
}


def get_adapter(spec: TargetSpec) -> TargetAdapter:
    adapter = ADAPTER_REGISTRY.get(spec.type)
    if adapter is None:
        raise ValueError(f"No adapter registered for target type: {spec.type!r}")
    return adapter
