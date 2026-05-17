"""Shared internal helpers for container-based tool implementations.

Provides script generation, output parsing, result comparison, and the
main container execution driver used by RunTargetTool, DiffBehaviorTool,
and FuzzTargetTool.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from tools.implementations.container.adapters import (
    InvocationResult, TargetSpec, TestCase, get_adapter,
)
from tools.implementations.container.runtime import ContainerLimits, ContainerSession, VolumeMount


def parse_test_cases(raw: list[dict]) -> list[TestCase]:
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


def mismatch_summary(oracle: InvocationResult, candidate: InvocationResult) -> str | None:
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


def build_container_script(
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


def parse_container_output(stdout: bytes) -> tuple[str | None, list[dict]]:
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


def run_in_container(
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
    script = build_container_script(build_cmds, "", cases, artifact_path, workspace)

    workspace_abs = str(Path(workspace).resolve())
    result = session.run(
        image=image,
        command=script,
        mounts=[VolumeMount(host_path=workspace_abs, container_path=workspace_abs, mode="ro")],
        limits=limits,
    )

    if result.timed_out:
        return "container timed out during build/execution", []

    build_error, raw_results = parse_container_output(result.stdout)
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
