# 0077 — Container-Based Dynamic Analysis & Differential Testing

## Problem

Static analysis (Ghidra decompilation) is insufficient for verifying binary reconstructions.
The agent produces code that compiles but diverges from the original binary's behavior —
wrong mode (ECB vs CBC), wrong key derivation, wrong padding — because Ghidra's output
is ambiguous and there is no feedback loop that forces correctness.

More broadly: as the agent takes on more RE tasks (native binaries, JVM bytecode, APKs,
servers, WASM modules), we need a general way to say "does this candidate behave identically
to this oracle?" regardless of target type.

## Core Abstraction: Oracle vs Candidate

The differential testing model is target-agnostic:

```
oracle    — the ground truth (original binary, running server, reference implementation)
candidate — what we're verifying (reconstructed source, reimplemented service, clone)
test case — a stimulus: args + stdin for CLI tools, HTTP request for servers, etc.
result    — oracle output vs candidate output, match/mismatch, structured diff
```

This pattern is identical whether comparing:
- A native macOS binary vs reconstructed C source
- A Java APK vs a reimplemented Python service
- A C2 server vs a behavioral clone
- A compiled Rust binary vs a decompiled Go port
- A network protocol server vs a spec-compliant reimplementation

The container toolset implements this abstraction. Target-specific details live in
**adapters** — plugins that know how to build and invoke a particular kind of artifact.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Agent (plan steps)                       │
│   calls: run_target | diff_behavior | fuzz_target            │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                  Dynamic Analysis Tools                       │
│   run_target(spec, test_cases)                               │
│   diff_behavior(oracle_spec, candidate_spec, test_cases)     │
│   fuzz_target(oracle_spec, candidate_spec, strategy)         │
└───────────┬───────────────────────────────────┬─────────────┘
            │ resolves adapter                  │ uses
┌───────────▼─────────────┐      ┌──────────────▼─────────────┐
│    Target Adapter        │      │    Container Runtime        │
│  (pluggable per type)    │      │  ContainerSession.run()     │
│                          │      │  image, mounts, limits,     │
│  NativeBinaryAdapter     │      │  timeout, result capture    │
│  JVMAdapter              │      └─────────────────────────────┘
│  PythonAdapter           │
│  ServerAdapter           │
│  (future: WASM, APK...)  │
└──────────────────────────┘
```

## Key Data Structures

### TargetSpec
Describes what to run — the oracle or the candidate.

```python
@dataclass
class TargetSpec:
    type: Literal["native_binary", "c_source", "cpp_source",
                  "python_source", "jar", "apk", "server"]
    path: str                          # path to binary or source file
    build_flags: list[str] = []        # compiler flags, JVM args, etc.
    image: str | None = None           # override default Docker image for this type
```

### TestCase
A single stimulus. Covers CLI tools and network services.

```python
@dataclass
class TestCase:
    id: str
    # CLI tools
    args: list[str] = []
    stdin: bytes | None = None
    env: dict[str, str] = {}
    # Network services
    request: dict | None = None        # {method, path, headers, body}
    timeout_seconds: float = 10.0
```

### DiffReport
What tools return to the agent.

```python
@dataclass
class CaseResult:
    case_id: str
    oracle: InvocationResult           # {stdout, stderr, exit_code, duration_ms}
    candidate: InvocationResult
    match: bool
    mismatch_summary: str | None       # "stdout differs: length 16 vs 24"

@dataclass
class DiffReport:
    all_match: bool
    total: int
    matching: int
    build_error: str | None            # compile/build failure before any tests ran
    cases: list[CaseResult]
    duration_ms: int
```

## Container Design

Each tool invocation is a **single, stateless container run**:

```
docker run --rm
  --network none
  --read-only
  --tmpfs /tmp:size=256m
  --memory 256m
  --pids-limit 64
  --cpus 1.0
  --security-opt no-new-privileges
  -v {workspace}:{workspace}:ro      # source and oracle binaries, read-only
  {image}
  bash -c "{adapter_script}"
```

The adapter script (generated per target type):
1. Copies source into `/tmp` (writable)
2. Builds the candidate if needed
3. Runs oracle and candidate against each test case
4. Emits JSON result to stdout
5. Exits — container torn down automatically (`--rm`)

No persistent containers. No container management state. No daemon to monitor.
The tool call blocks until the container exits and returns the JSON result.

## Target Adapters

Each adapter implements:

```python
class TargetAdapter:
    default_image: str

    def build_script(self, spec: TargetSpec) -> str:
        """Shell commands to build the candidate inside the container.
        Returns path to the runnable artifact."""

    def invoke_command(self, artifact_path: str, case: TestCase) -> str:
        """Shell command to run one test case. stdout/stderr captured by framework."""
```

### Planned Adapters

| Adapter | Image | Build step | Invoke |
|---|---|---|---|
| `NativeBinaryAdapter` | `gcc:12` | `cc -o /tmp/candidate {source}` | `/tmp/candidate {args}` |
| `NativeBinaryAdapter` (pre-built) | `ubuntu:22.04` | copy only | `{binary} {args}` |
| `PythonAdapter` | `python:3.11-slim` | `pip install -r requirements.txt` (optional) | `python3 {source} {args}` |
| `JVMAdapter` | `openjdk:17-slim` | `javac {source}` or none for JAR | `java -jar {jar} {args}` |
| `ServerAdapter` | configurable | start process, wait for port | HTTP request via `curl` |

New adapters are added by implementing the two methods above and registering in an
`ADAPTER_REGISTRY` dict keyed by `TargetSpec.type`. No other changes required.

## Tools Exposed to Agent

### `run_target(spec, test_cases, label=None)`
Run a single target (oracle or candidate) against test cases. Returns raw results
per case — no comparison. Used for exploration ("how does this binary behave?").

### `diff_behavior(oracle_spec, candidate_spec, test_cases)`
Run both targets against the same test cases in one container. Returns a `DiffReport`.
Primary tool for the reconstruction loop. The agent reads the diff and patches the source.

### `fuzz_target(oracle_spec, candidate_spec, strategy, n_cases, seed_cases)`
Generate test cases automatically (random, boundary, mutation-based) and diff.
Returns a `DiffReport` with the generated cases included. Used for edge case discovery
when the user doesn't want to specify test cases manually.

## The Reconstruction Loop

How "iterate on the code" works step by step:

```
[User]: iterate on _tests/run_2/proc_clone.c against _tests/proc

[Agent plan]:
  Step 1: load or generate test_cases.json for _tests/proc
  Step 2: diff_behavior(
            oracle=TargetSpec("native_binary", "_tests/proc"),
            candidate=TargetSpec("c_source", "_tests/run_2/proc_clone.c"),
            test_cases=loaded_cases
          )
          → DiffReport: 6/10 cases diverging

  Step 3: [conversation] read DiffReport, identify bug pattern
          "1-byte input: oracle=16 bytes, candidate=24 bytes → padding adds extra block"

  Step 4: write_file — patch proc_clone.c

  Step 5: diff_behavior(...) same call, same cases
          → DiffReport: 3/10 diverging

  Step 6: [conversation] "multi-block inputs still diverge, single-block match
          → chaining bug, not padding"

  Step 7: write_file — patch CBC chaining

  Step 8: diff_behavior(...)
          → DiffReport: all_match=true

  Step 9: synthesize — "reconstruction verified, all 10 test cases pass"
```

The agent never re-runs Ghidra during this loop. The decompilation is already in context.
The diff is the only new information per iteration.

## Test Case Generation

Test cases are generated once during the initial `deep-disassembly` run and saved as
`_tests/run_N/test_spec.json` alongside the source file. The workflow derives them from:

- CLI usage string (from `strings` output) — defines argument structure
- Block size (from `ghidra_find_constants`) — drives boundary test sizes
- Algorithm type — TEA → encryption/decryption round-trips; hash → determinism tests

Standard battery for a CLI encryption tool (always included):

| ID | Args | Purpose |
|---|---|---|
| enc_1byte | `-e pass a` | Padding: 1 byte padded to 8 |
| enc_7byte | `-e pass 1234567` | Padding: 7 bytes padded to 8 |
| enc_8byte | `-e pass 12345678` | Padding: exactly 1 block + full padding block |
| enc_10byte | `-e pass helloworld` | Padding: 10 bytes padded to 16 |
| enc_16byte | `-e pass 1234567890123456` | Padding: 2 blocks exactly |
| enc_shortpass | `-e ab helloworld` | Key derivation: short passphrase |
| enc_longpass | `-e abcdefghijklmnop helloworld` | Key derivation: 16-char passphrase |
| roundtrip_orig | encrypt then decrypt with oracle | Oracle self-consistency |
| roundtrip_cross_1 | oracle encrypt → candidate decrypt | Primary correctness test |
| roundtrip_cross_2 | candidate encrypt → oracle decrypt | Reverse correctness test |

## Stopping / Bounding

- Each container call: `timeout_seconds` (default 60s). Subprocess kills on timeout.
- Reconstruction loop: `max_iterations` plan config (default 20). Agent reports partial
  progress and asks user if not converged.
- `fuzz_target`: `n_cases` argument caps total cases (default 50).
- User `Ctrl-C`: kills the session process. No orphaned containers because each call
  blocks synchronously and Docker `--rm` ensures cleanup.

## Relationship to Existing Sandbox

The `SandboxManager` (bash_exec) and the container dynamic analysis toolset are
**completely separate systems**:

| | SandboxManager (bash_exec) | Dynamic Analysis Toolset |
|---|---|---|
| Purpose | Run general shell commands for the agent | Run controlled binary experiments |
| Backend | mac_sandbox_exec or host | Docker (always) |
| Invocation | Any bash_exec tool call | Explicit `diff_behavior`, `run_target`, etc. |
| Target type | Agent's own code, build tools, file ops | Potentially hostile binaries |
| Persistence | Stateless | Stateless |

Docker is **not** in the bash_exec sandbox chain. It is **only** used by this toolset.

## Phases

| Plan | Phase | Deliverable |
|---|---|---|
| 0077a | Container Runtime | `ContainerSession`, resource limits, result capture, image management |
| 0077b | Target Adapters | `TargetAdapter` base, `NativeBinaryAdapter` (source + pre-built), adapter registry |
| 0077c | Core Tools | `run_target`, `diff_behavior`, `fuzz_target` implementations |
| 0077d | Toolset Wiring | Register `container` toolset, routing descriptions, config |
| 0077e | Workflow Integration | Update `deep-disassembly` step 11, add `test-reconstruction` workflow, test spec generation |
