"""RunTargetTool — run a binary or source file against test cases in a container."""
from __future__ import annotations

import json
from pathlib import Path

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.container.adapters import TargetSpec, get_adapter
from tools.implementations.container.runtime import ContainerLimits, ContainerSession
from tools.implementations.container._helpers import parse_test_cases, run_in_container


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
        spec = TargetSpec(
            type=tool_input["type"],
            path=tool_input["path"],
            build_flags=tool_input.get("build_flags") or [],
        )
        cases = parse_test_cases(tool_input.get("test_cases") or [])
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
        build_error, results = run_in_container(spec, cases, session, limits, workspace)

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
