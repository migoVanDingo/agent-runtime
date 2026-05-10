"""Container-based dynamic analysis tools."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.container.adapters import (
    InvocationResult, TargetSpec, TestCase, get_adapter,
)
from tools.implementations.container.runtime import ContainerLimits, ContainerSession, VolumeMount


# ── Shared internals ────────────────────────────────────────────────────────

def _parse_test_cases(raw: list[dict]) -> list[TestCase]:
    cases = []
    for item in raw:
        cases.append(TestCase(
            id=item.get("id", f"case_{len(cases)}"),
            args=item.get("args", []),
            stdin=item.get("stdin"),
            env=item.get("env", {}),
            timeout_seconds=float(item.get("timeout_seconds", 10.0)),
        ))
    return cases


def _mismatch_summary(oracle: InvocationResult, candidate: InvocationResult) -> str | None:
    if oracle.exit_code != candidate.exit_code:
        return f"exit_code differs: oracle={oracle.exit_code}, candidate={candidate.exit_code}"
    if oracle.stdout != candidate.stdout:
        lo, lc = len(oracle.stdout), len(candidate.stdout)
        if lo != lc:
            ob = lo // 2 if all(c in "0123456789abcdefABCDEF" for c in oracle.stdout.strip()) else lo
            cb = lc // 2 if all(c in "0123456789abcdefABCDEF" for c in candidate.stdout.strip()) else lc
            return f"stdout differs: oracle={lo} chars ({ob} bytes), candidate={lc} chars ({cb} bytes)"
        for i, (a, b) in enumerate(zip(oracle.stdout, candidate.stdout)):
            if a != b:
                return f"stdout differs at char {i}: oracle={repr(a)}, candidate={repr(b)}"
        return "stdout differs"
    if oracle.stderr != candidate.stderr:
        return "stderr differs"
    return None


def _build_container_script(
    build_cmds: str,
    invoke_cmd_template: str,
    cases: list[TestCase],
    artifact_path: str,
    workspace: str,
) -> str:
    """Generate a bash script that runs inside the container.
    Emits one JSON object per line between sentinels."""
    case_data = json.dumps([
        {"id": c.id, "args": c.args, "stdin": c.stdin or "", "timeout": c.timeout_seconds}
        for c in cases
    ])
    return textwrap.dedent(f"""
        set -e
        WORKSPACE={json.dumps(workspace)}

        # Build step
        {build_cmds}
        echo '__BUILD_OK__'

        # Run test cases via embedded Python for safe JSON output
        python3 - <<'PYEOF'
import subprocess, json, sys, time

cases = {case_data}
artifact = {json.dumps(artifact_path)}
results = []

for case in cases:
    start = time.monotonic()
    try:
        r = subprocess.run(
            [artifact] + case['args'],
            capture_output=True,
            timeout=case['timeout'],
            input=case['stdin'].encode() if case['stdin'] else None,
        )
        results.append({{
            'id': case['id'],
            'stdout': r.stdout.decode('utf-8', errors='replace'),
            'stderr': r.stderr.decode('utf-8', errors='replace'),
            'exit_code': r.returncode,
            'timed_out': False,
            'duration_ms': int((time.monotonic() - start) * 1000),
        }})
    except subprocess.TimeoutExpired:
        results.append({{
            'id': case['id'], 'stdout': '', 'stderr': '',
            'exit_code': None, 'timed_out': True,
            'duration_ms': int((time.monotonic() - start) * 1000),
        }})
    except Exception as e:
        results.append({{
            'id': case['id'], 'stdout': '', 'stderr': str(e),
            'exit_code': 1, 'timed_out': False,
            'duration_ms': 0,
        }})

print('__RESULTS_START__')
for r in results:
    print(json.dumps(r))
print('__RESULTS_END__')
PYEOF
    """).strip()


def _parse_container_output(stdout: bytes) -> tuple[str | None, list[dict]]:
    """Parse build error and per-case results from container stdout.
    Returns (build_error | None, list of result dicts)."""
    text = stdout.decode("utf-8", errors="replace")

    if "__BUILD_OK__" not in text:
        build_section = text.split("__BUILD_OK__")[0] if "__BUILD_OK__" in text else text
        return build_section.strip() or "build failed (no output)", []

    results = []
    in_results = False
    for line in text.splitlines():
        if line == "__RESULTS_START__":
            in_results = True
            continue
        if line == "__RESULTS_END__":
            break
        if in_results and line.strip():
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    return None, results


def _run_in_container(
    spec: TargetSpec,
    cases: list[TestCase],
    session: ContainerSession,
    limits: ContainerLimits,
    workspace: str,
) -> tuple[str | None, list[InvocationResult]]:
    """Compile (if needed) and run cases in a container. Returns (build_error, results)."""
    adapter = get_adapter(spec)
    image = adapter.image_for(spec)
    artifact_path = "/tmp/_arc_candidate"
    spec_abs = str(Path(spec.path).resolve())

    build_cmds = adapter.build_commands(
        TargetSpec(type=spec.type, path=spec_abs, build_flags=spec.build_flags),
        artifact_path,
    )
    script = _build_container_script(build_cmds, "", cases, artifact_path, workspace)

    workspace_abs = str(Path(workspace).resolve())
    result = session.run(
        image=image,
        command=script,
        mounts=[VolumeMount(host_path=workspace_abs, container_path=workspace_abs, mode="ro")],
        limits=limits,
    )

    if result.timed_out:
        return "container timed out during build/execution", []

    build_error, raw_results = _parse_container_output(result.stdout)
    if build_error:
        stderr_hint = result.stderr.decode("utf-8", errors="replace").strip()
        return f"{build_error}\n{stderr_hint}".strip(), []

    invocations = []
    for r in raw_results:
        invocations.append(InvocationResult(
            stdout=r.get("stdout", ""),
            stderr=r.get("stderr", ""),
            exit_code=r.get("exit_code"),
            timed_out=r.get("timed_out", False),
            duration_ms=r.get("duration_ms", 0),
        ))
    return None, invocations


# ── RunTargetTool ────────────────────────────────────────────────────────────

class RunTargetTool(BaseTool):
    name = "run_target"
    description = (
        "Run a binary, script, or source file against a list of test cases and return "
        "the output per case. Use this to explore how a binary behaves with various inputs. "
        "For native_binary type, the binary runs on the host. For c_source/cpp_source/"
        "python_source, it is compiled/run inside a Docker container."
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary or source file"),
                "type": ToolProperty(
                    type="string",
                    description="Target type: native_binary | c_source | cpp_source | python_source",
                ),
                "test_cases": ToolProperty(
                    type="array",
                    description='List of test cases. Each: {"id": "...", "args": [...], "stdin": "..."}',
                    items={"type": "object"},
                ),
                "build_flags": ToolProperty(
                    type="array",
                    description="Optional compiler flags (for c_source/cpp_source)",
                    items={"type": "string"},
                ),
                "timeout_seconds": ToolProperty(
                    type="number",
                    description="Total timeout for the container invocation (default 60)",
                ),
            },
            required=["path", "type"],
        )

    def execute(self, tool_input: dict) -> str:
        from app_config import config as _cfg
        spec = TargetSpec(
            type=tool_input["type"],
            path=tool_input["path"],
            build_flags=tool_input.get("build_flags") or [],
        )
        cases = _parse_test_cases(tool_input.get("test_cases") or [])
        if not cases:
            return json.dumps({
                "error": "test_cases is required and cannot be empty",
                "usage": 'Provide a list like: [{"id": "test1", "args": ["-e", "pass", "hello"]}]',
            })
        timeout = float(tool_input.get("timeout_seconds") or 60.0)
        workspace = str(Path(".").resolve())

        adapter = get_adapter(spec)

        if adapter.runs_locally:
            results = [adapter.run_locally(spec, c) for c in cases]
            output = {
                "cases": [
                    {
                        "id": c.id, "args": c.args,
                        "stdout": r.stdout, "stderr": r.stderr,
                        "exit_code": r.exit_code, "timed_out": r.timed_out,
                        "duration_ms": r.duration_ms,
                    }
                    for c, r in zip(cases, results)
                ],
                "isolation": "host",
            }
            return json.dumps(output, indent=2)

        session = ContainerSession()
        if not session.available():
            return json.dumps({"error": "no OCI runtime available (docker/podman not found or daemon not running)"})

        limits = ContainerLimits(timeout_seconds=timeout)
        build_error, results = _run_in_container(spec, cases, session, limits, workspace)

        if build_error:
            return json.dumps({"build_error": build_error, "cases": []})

        return json.dumps({
            "cases": [
                {
                    "id": c.id, "args": c.args,
                    "stdout": r.stdout, "stderr": r.stderr,
                    "exit_code": r.exit_code, "timed_out": r.timed_out,
                    "duration_ms": r.duration_ms,
                }
                for c, r in zip(cases, results)
            ],
            "isolation": "container",
        }, indent=2)


# ── DiffBehaviorTool ─────────────────────────────────────────────────────────

class DiffBehaviorTool(BaseTool):
    name = "diff_behavior"
    description = (
        "Run an oracle binary and a candidate (source or binary) against the same test cases "
        "and return a structured diff. The oracle runs on the host; the candidate is compiled "
        "and run inside a Docker container. Use this to verify that a reconstructed program "
        "matches the original binary's behavior. Iterate: if all_match=false, read the "
        "mismatch_summary for each failing case to identify the bug, fix the source, then call again."
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "oracle_path": ToolProperty(type="string", description="Path to the original binary (runs on host)"),
                "oracle_type": ToolProperty(type="string", description="Oracle type: native_binary"),
                "candidate_path": ToolProperty(type="string", description="Path to the candidate binary or source file"),
                "candidate_type": ToolProperty(
                    type="string",
                    description="Candidate type: c_source | cpp_source | python_source | native_binary",
                ),
                "test_cases": ToolProperty(
                    type="array",
                    description='List of test cases. Each: {"id": "...", "args": [...], "stdin": "..."}',
                    items={"type": "object"},
                ),
                "candidate_build_flags": ToolProperty(
                    type="array",
                    description="Optional compiler flags for the candidate",
                    items={"type": "string"},
                ),
                "timeout_seconds": ToolProperty(
                    type="number",
                    description="Total container timeout in seconds (default 120)",
                ),
            },
            required=["oracle_path", "oracle_type", "candidate_path", "candidate_type"],
        )

    def execute(self, tool_input: dict) -> str:
        oracle_spec = TargetSpec(
            type=tool_input["oracle_type"],
            path=tool_input["oracle_path"],
        )
        candidate_spec = TargetSpec(
            type=tool_input["candidate_type"],
            path=tool_input["candidate_path"],
            build_flags=tool_input.get("candidate_build_flags") or [],
        )
        cases = _parse_test_cases(tool_input.get("test_cases") or [])
        if not cases:
            return json.dumps({
                "error": "test_cases is required and cannot be empty",
                "usage": 'Provide a list like: [{"id": "enc_hello", "args": ["-e", "pass", "helloworld"]}]',
            })
        timeout = float(tool_input.get("timeout_seconds") or 120.0)
        workspace = str(Path(".").resolve())

        oracle_adapter = get_adapter(oracle_spec)
        candidate_adapter = get_adapter(candidate_spec)

        # Run oracle
        if oracle_adapter.runs_locally:
            oracle_results = {c.id: oracle_adapter.run_locally(oracle_spec, c) for c in cases}
        else:
            session = ContainerSession()
            if not session.available():
                return json.dumps({"error": "no OCI runtime available for oracle execution"})
            _, raw = _run_in_container(oracle_spec, cases, session, ContainerLimits(timeout_seconds=timeout), workspace)
            oracle_results = {c.id: r for c, r in zip(cases, raw)}

        # Run candidate
        candidate_results: dict[str, InvocationResult] = {}
        build_error: str | None = None

        if candidate_adapter.runs_locally:
            candidate_results = {c.id: candidate_adapter.run_locally(candidate_spec, c) for c in cases}
        else:
            session = ContainerSession()
            if not session.available():
                return json.dumps({"error": "no OCI runtime available for candidate execution"})
            build_error, raw = _run_in_container(
                candidate_spec, cases, session,
                ContainerLimits(timeout_seconds=timeout), workspace,
            )
            if build_error is None:
                candidate_results = {c.id: r for c, r in zip(cases, raw)}

        if build_error:
            return json.dumps({"all_match": False, "build_error": build_error, "cases": []}, indent=2)

        # Build diff report
        case_results = []
        for case in cases:
            oracle_r = oracle_results.get(case.id)
            candidate_r = candidate_results.get(case.id)
            if oracle_r is None or candidate_r is None:
                continue
            summary = _mismatch_summary(oracle_r, candidate_r)
            match = summary is None
            case_results.append({
                "id": case.id,
                "args": case.args,
                "oracle_stdout": oracle_r.stdout,
                "oracle_stderr": oracle_r.stderr,
                "oracle_exit_code": oracle_r.exit_code,
                "candidate_stdout": candidate_r.stdout,
                "candidate_stderr": candidate_r.stderr,
                "candidate_exit_code": candidate_r.exit_code,
                "match": match,
                "mismatch_summary": summary,
            })

        matching = sum(1 for r in case_results if r["match"])
        return json.dumps({
            "all_match": matching == len(case_results),
            "total": len(case_results),
            "matching": matching,
            "build_error": None,
            "cases": case_results,
        }, indent=2)


# ── FuzzTargetTool ───────────────────────────────────────────────────────────

class FuzzTargetTool(BaseTool):
    name = "fuzz_target"
    description = (
        "Automatically generate test cases and diff oracle vs candidate behavior. "
        "strategy='boundary' generates inputs at block-size boundaries (1,7,8,9,15,16,17 bytes). "
        "strategy='random' generates random printable strings. "
        "strategy='mutation' mutates provided seed_cases. "
        "Returns the same DiffReport as diff_behavior plus the generated test cases."
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "oracle_path": ToolProperty(type="string", description="Path to the original binary"),
                "oracle_type": ToolProperty(type="string", description="Oracle type: native_binary"),
                "candidate_path": ToolProperty(type="string", description="Path to candidate binary or source"),
                "candidate_type": ToolProperty(type="string", description="Candidate type"),
                "strategy": ToolProperty(
                    type="string",
                    description="Case generation strategy: boundary | random | mutation",
                ),
                "arg_template": ToolProperty(
                    type="array",
                    description='Arg template with {data} and {passphrase} placeholders. E.g. ["-e", "{passphrase}", "{data}"]',
                    items={"type": "string"},
                ),
                "n_cases": ToolProperty(type="integer", description="Number of cases to generate (default 20)"),
                "seed_cases": ToolProperty(type="array", description="Seed cases for mutation strategy", items={"type": "object"}),
                "block_size": ToolProperty(type="integer", description="Block size for boundary strategy (default 8)"),
                "candidate_build_flags": ToolProperty(type="array", description="Compiler flags for candidate", items={"type": "string"}),
                "timeout_seconds": ToolProperty(type="number", description="Container timeout (default 120)"),
            },
            required=["oracle_path", "oracle_type", "candidate_path", "candidate_type", "strategy", "arg_template"],
        )

    def execute(self, tool_input: dict) -> str:
        import random
        import string

        strategy = tool_input.get("strategy", "boundary")
        arg_template = tool_input.get("arg_template", [])
        n_cases = int(tool_input.get("n_cases") or 20)
        block_size = int(tool_input.get("block_size") or 8)
        seed_cases = tool_input.get("seed_cases") or []

        passphrase = "testpass"

        def make_case(case_id: str, data: str) -> dict:
            args = [
                a.replace("{data}", data).replace("{passphrase}", passphrase)
                for a in arg_template
            ]
            return {"id": case_id, "args": args}

        generated: list[dict] = []

        if strategy == "boundary":
            for size in [1, block_size - 1, block_size, block_size + 1,
                         2 * block_size - 1, 2 * block_size, 2 * block_size + 1]:
                if size <= 0:
                    continue
                data = ("A" * size)[:size]
                generated.append(make_case(f"boundary_{size}b", data))
                if len(generated) >= n_cases:
                    break

        elif strategy == "random":
            chars = string.ascii_letters + string.digits
            for i in range(n_cases):
                size = random.randint(1, 64)
                data = "".join(random.choices(chars, k=size))
                generated.append(make_case(f"rand_{i}", data))

        elif strategy == "mutation":
            base_cases = seed_cases or [{"id": "seed", "args": arg_template}]
            for i, seed in enumerate(base_cases[:n_cases]):
                args = seed.get("args", [])
                mutated = list(args)
                if mutated:
                    idx = random.randint(0, len(mutated) - 1)
                    mutated[idx] = mutated[idx] + random.choice(string.printable[:32])
                generated.append({"id": f"mut_{i}", "args": mutated})

        # Delegate to diff_behavior
        diff_input = {
            "oracle_path": tool_input["oracle_path"],
            "oracle_type": tool_input["oracle_type"],
            "candidate_path": tool_input["candidate_path"],
            "candidate_type": tool_input["candidate_type"],
            "test_cases": generated,
            "candidate_build_flags": tool_input.get("candidate_build_flags"),
            "timeout_seconds": tool_input.get("timeout_seconds", 120),
        }
        diff_result = json.loads(DiffBehaviorTool().execute(diff_input))
        diff_result["generated_cases"] = generated
        return json.dumps(diff_result, indent=2)
