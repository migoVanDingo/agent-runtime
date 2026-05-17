# 0077b — Target Adapters

## Goal
Define the `TargetAdapter` plugin interface and implement the first concrete adapter:
`NativeBinaryAdapter` (handles pre-built binaries and C/C++ source files).
All future target types (JVM, Python, server, APK) follow the same interface.

## New File: `src/tools/implementations/container/adapters.py`

### TargetSpec
```python
@dataclass
class TargetSpec:
    type: Literal[
        "native_binary",   # pre-compiled binary, run directly
        "c_source",        # C source file, compile with gcc
        "cpp_source",      # C++ source, compile with g++
        "python_source",   # Python script, run with python3
        "jar",             # JAR file, run with java -jar
        "apk",             # Android APK (future)
        "server",          # Long-running process (future)
    ]
    path: str                     # absolute or workspace-relative path
    build_flags: list[str] = field(default_factory=list)
    image: str | None = None      # override default image for this type
```

### InvocationResult
```python
@dataclass
class InvocationResult:
    stdout: str          # decoded with errors='replace'
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int
```

### TargetAdapter (base)
```python
class TargetAdapter:
    default_image: str = "ubuntu:22.04"

    def image_for(self, spec: TargetSpec) -> str:
        return spec.image or self.default_image

    def build_commands(self, spec: TargetSpec, artifact_path: str) -> str:
        """Shell commands to build the candidate. artifact_path is where
        the runnable result should end up (e.g. /tmp/candidate).
        Return empty string if no build step needed."""
        return ""

    def invoke_command(self, artifact_path: str, case: "TestCase") -> str:
        """Shell command to run one test case. Framework captures stdout/stderr."""
        raise NotImplementedError
```

### TestCase
```python
@dataclass
class TestCase:
    id: str
    args: list[str] = field(default_factory=list)
    stdin: str | None = None          # passed via heredoc inside container
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 10.0
    # Future: request: dict | None = None  (for ServerAdapter)
```

### NativeBinaryAdapter
```python
class NativeBinaryAdapter(TargetAdapter):
    default_image = "gcc:12"

    def build_commands(self, spec: TargetSpec, artifact_path: str) -> str:
        if spec.type == "native_binary":
            # No build — binary is already compiled, just copy to /tmp for execution
            return f"cp {spec.path} {artifact_path} && chmod +x {artifact_path}"
        compiler = "cc" if spec.type == "c_source" else "c++"
        flags = " ".join(spec.build_flags) if spec.build_flags else ""
        return f"{compiler} {flags} -o {artifact_path} {spec.path}"

    def invoke_command(self, artifact_path: str, case: TestCase) -> str:
        args = " ".join(shlex.quote(a) for a in case.args)
        if case.stdin:
            return f"printf %s {shlex.quote(case.stdin)} | {artifact_path} {args}"
        return f"{artifact_path} {args}"
```

### Adapter Registry
```python
ADAPTER_REGISTRY: dict[str, TargetAdapter] = {
    "native_binary": NativeBinaryAdapter(),
    "c_source":      NativeBinaryAdapter(),
    "cpp_source":    NativeBinaryAdapter(),
    # "python_source": PythonAdapter(),   # phase B+
    # "jar":           JVMAdapter(),       # phase B+
    # "server":        ServerAdapter(),    # phase B+
}

def get_adapter(spec: TargetSpec) -> TargetAdapter:
    adapter = ADAPTER_REGISTRY.get(spec.type)
    if adapter is None:
        raise ValueError(f"No adapter registered for target type: {spec.type!r}")
    return adapter
```

## Future Adapters (not implemented in this phase)

### PythonAdapter
```python
class PythonAdapter(TargetAdapter):
    default_image = "python:3.11-slim"

    def build_commands(self, spec, artifact_path):
        # No compile — artifact_path IS the source path
        return f"cp {spec.path} {artifact_path}"

    def invoke_command(self, artifact_path, case):
        args = " ".join(shlex.quote(a) for a in case.args)
        return f"python3 {artifact_path} {args}"
```

### JVMAdapter
```python
class JVMAdapter(TargetAdapter):
    default_image = "openjdk:17-slim"

    def build_commands(self, spec, artifact_path):
        if spec.type == "jar":
            return f"cp {spec.path} {artifact_path}.jar"
        # Java source — compile
        return f"javac -d /tmp/classes {spec.path}"

    def invoke_command(self, artifact_path, case):
        args = " ".join(shlex.quote(a) for a in case.args)
        return f"java -jar {artifact_path}.jar {args}"
```

### ServerAdapter (future, more complex)
Starts a long-running process, waits for port, sends HTTP requests, tears down.
Will require a different container lifecycle (not single-shot bash -c).
Designed but not implemented until needed.

## What this phase does NOT touch
- Tool registration (phase D)
- The actual `diff_behavior` / `run_target` tool implementations (phase C)
- Config parsing (phase D)
