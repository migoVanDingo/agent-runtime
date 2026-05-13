# 0077d — Toolset Wiring

## Goal
Register the container toolset so the agent can discover and route to it. Add config
section. Mark the toolset as conditionally available (only if Docker daemon is reachable).

## New File: `src/tools/implementations/container/__init__.py`
Empty — marks it as a package.

## New File: `src/tools/implementations/container/toolset.py`

```python
from tools.base import Toolset, ToolDefinition
from tools.implementations.container.tools import diff_behavior, run_target, fuzz_target
from tools.implementations.container.runtime import ContainerSession

def _docker_available() -> bool:
    return ContainerSession.available()

CONTAINER_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="run_target",
        fn=run_target,
        description=(
            "Run a binary, script, or compiled source inside a Docker container "
            "against a list of test cases. Returns stdout/stderr/exit_code per case. "
            "Used for exploring binary behavior or verifying a single target."
        ),
        enabled=_docker_available,   # conditionally registered
    ),
    ToolDefinition(
        name="diff_behavior",
        fn=diff_behavior,
        description=(
            "Run an oracle binary and a candidate (binary or source file) against "
            "the same test cases inside Docker. Returns a structured diff showing "
            "which inputs produce different outputs. Primary tool for verifying "
            "that a reconstructed program matches the original binary's behavior."
        ),
        enabled=_docker_available,
    ),
    ToolDefinition(
        name="fuzz_target",
        fn=fuzz_target,
        description=(
            "Automatically generate test cases (boundary sizes, random inputs, "
            "or mutations) and diff oracle vs candidate. Returns diverging cases. "
            "Used for edge case discovery without manually specifying test inputs."
        ),
        enabled=_docker_available,
    ),
]

container_toolset = Toolset(
    name="container",
    tools=CONTAINER_TOOLS,
)
```

## Register in `src/tools/toolsets.py`

```python
from tools.implementations.container.toolset import container_toolset

ALL_TOOLSETS = [
    ...existing...,
    container_toolset,
]
```

## Routing description in `config.yml`

```yaml
routing:
  toolset_descriptions:
    ...existing...
    container: >-
      differential testing docker container run binary oracle candidate compare behavior
      verify reconstruction iterate fix round-trip test fuzz edge cases compile source
      native binary java jar python server dynamic analysis behavioral equivalence
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

## Config model additions to `src/config.py`

```python
@dataclass
class ContainerLimitsConfig:
    timeout_seconds: float = 60.0
    memory: str = "256m"
    cpus: float = 1.0
    pids_limit: int = 64
    network: str = "none"

@dataclass
class ContainerImagesConfig:
    native: str = "gcc:12"
    jvm: str = "openjdk:17-slim"
    python: str = "python:3.11-slim"
    base: str = "ubuntu:22.04"

@dataclass
class ContainerConfig:
    limits: ContainerLimitsConfig = field(default_factory=ContainerLimitsConfig)
    images: ContainerImagesConfig = field(default_factory=ContainerImagesConfig)

# Added to AppConfig:
@dataclass
class AppConfig:
    ...
    container: ContainerConfig = field(default_factory=ContainerConfig)
```

## Conditional registration

The toolset is only registered if `ContainerSession.available()` returns True at startup.
If Docker is not running, the `container` toolset simply doesn't appear in the tool
registry — no error, no warning. The routing system won't suggest container tools for
requests if they aren't registered.

Log at startup:
```
[INFO] tools.registry: Registered toolset: container (3 tools)     # docker available
[INFO] tools.registry: Skipped toolset: container (docker unavailable)  # no docker
```
