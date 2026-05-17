# 0077a — Container Runtime

## Goal
Low-level layer that knows nothing about targets or test cases. Wraps an OCI container
runtime (Docker or Podman, whichever is available) with resource limits, volume mounting,
timeout, and result capture. Everything above this layer uses this as its only interface.

## Runtime Detection

```python
# Priority order — first found wins.
# nerdctl (containerd CLI) and finch (AWS Lima-based) use identical syntax.
OCI_RUNTIME_PRIORITY = ("docker", "podman", "nerdctl", "finch")

def find_oci_runtime() -> str | None:
    return next((r for r in OCI_RUNTIME_PRIORITY if shutil.which(r)), None)
```

`ContainerSession` stores the detected runtime name at instantiation and uses it as
the executable for all commands. No other code changes are needed to support Podman —
its CLI flags are identical to Docker's for everything we use.

## Fallback tiers when no OCI runtime is found

| Available | Behavior |
|---|---|
| Docker / Podman / nerdctl / finch | Full container isolation — preferred |
| None, macOS | mac_sandbox_exec fallback — safe for candidate (we compiled it), user-approval required for oracle binary |
| None, no sandbox | Host execution — explicit warning injected into tool result |

The tool returns an `isolation` field in its result so the agent can surface the
actual isolation level to the user when relevant.

## New File: `src/tools/implementations/container/runtime.py`

### ContainerLimits
```python
@dataclass
class ContainerLimits:
    timeout_seconds: float = 60.0
    memory: str = "256m"
    cpus: float = 1.0
    pids_limit: int = 64
    network: Literal["none", "bridge"] = "none"
```

### VolumeMount
```python
@dataclass
class VolumeMount:
    host_path: str   # absolute path
    container_path: str
    mode: Literal["ro", "rw"] = "ro"
```

### ContainerResult
```python
@dataclass
class ContainerResult:
    stdout: bytes
    stderr: bytes
    exit_code: int | None   # None = timed out
    timed_out: bool
    duration_ms: int
    runtime: str            # "docker", "podman", etc. — for observability
    isolation: str          # "container", "mac_sandbox", "host"
```

### ContainerSession
```python
class ContainerSession:
    def __init__(self) -> None:
        self._runtime = find_oci_runtime()  # None if unavailable

    @staticmethod
    def available() -> bool:
        return find_oci_runtime() is not None

    def run(
        self,
        image: str,
        command: str,                  # passed to /bin/bash -c
        mounts: list[VolumeMount] = [],
        limits: ContainerLimits | None = None,
        env: dict[str, str] = {},
    ) -> ContainerResult:
        if self._runtime is None:
            raise RuntimeError(
                "No OCI container runtime found. "
                "Install Docker Desktop, Podman Desktop, or nerdctl."
            )
        limits = limits or ContainerLimits()
        cmd = self._build_command(image, command, mounts, limits, env)
        return self._run_subprocess(cmd, limits.timeout_seconds)

    def ensure_image(self, image: str) -> None:
        """Pull image if not cached locally."""
        if self._runtime is None:
            return
        subprocess.run([self._runtime, "pull", image], check=False, capture_output=True)

    def _build_command(self, image, command, mounts, limits, env) -> list[str]:
        cmd = [
            self._runtime, "run", "--rm",
            "--network", limits.network,
            "--read-only",
            "--tmpfs", "/tmp:size=128m",
            "--memory", limits.memory,
            f"--pids-limit={limits.pids_limit}",
            f"--cpus={limits.cpus}",
            "--security-opt", "no-new-privileges",
        ]
        for m in mounts:
            host = str(Path(m.host_path).resolve())
            cmd += ["-v", f"{host}:{m.container_path}:{m.mode}"]
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [image, "/bin/bash", "-c", command]
        return cmd

    def _run_subprocess(self, cmd: list[str], timeout: float) -> ContainerResult:
        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
            )
            return ContainerResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                timed_out=False,
                duration_ms=int((time.monotonic() - start) * 1000),
                runtime=cmd[0],
                isolation="container",
            )
        except subprocess.TimeoutExpired as e:
            return ContainerResult(
                stdout=e.stdout or b"",
                stderr=e.stderr or b"",
                exit_code=None,
                timed_out=True,
                duration_ms=int((time.monotonic() - start) * 1000),
                runtime=cmd[0],
                isolation="container",
            )
```

## Config additions to `config.yml`

```yaml
container:
  limits:
    timeout_seconds: 60
    memory: "256m"
    cpus: 1.0
    pids_limit: 64
    network: "none"
  images:
    native: "gcc:12"
    jvm: "openjdk:17-slim"
    python: "python:3.11-slim"
    base: "ubuntu:22.04"
```

## Verification (manual, in a fresh session)
```python
session = ContainerSession()
print(session._runtime)   # "docker" or "podman" or None

result = session.run("ubuntu:22.04", "echo hello && echo world >&2")
assert result.stdout == b"hello\n"
assert result.stderr == b"world\n"
assert result.exit_code == 0

result = session.run("ubuntu:22.04", "sleep 100",
                     limits=ContainerLimits(timeout_seconds=2))
assert result.timed_out
```
