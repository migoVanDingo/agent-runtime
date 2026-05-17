"""DiffBehaviorTool — compare oracle vs candidate behavior across test cases."""
from __future__ import annotations

import json
from pathlib import Path

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.container.adapters import InvocationResult, TargetSpec, get_adapter
from tools.implementations.container.runtime import ContainerLimits, ContainerSession
from tools.implementations.container._helpers import (
    mismatch_summary,
    parse_test_cases,
    run_in_container,
)


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
        cases = parse_test_cases(tool_input.get("test_cases") or [])
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
            _, raw = run_in_container(oracle_spec, cases, session, ContainerLimits(timeout_seconds=timeout), workspace)
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
            build_error, raw = run_in_container(
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
            summary = mismatch_summary(oracle_r, candidate_r)
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
