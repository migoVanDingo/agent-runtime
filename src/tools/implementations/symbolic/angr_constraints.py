import os
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.symbolic.angr_runner import angr_available, scaled_timeout, run_angr_script
from app_config import config

_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "constraints.py")


class AngrConstraintsTool(BaseTool):
    name = "angr_constraints"
    description = (
        "Dump the path constraints that must hold for execution to reach a target address. "
        "Shows what conditions (comparisons, checks) are required to reach the target. "
        "Useful for understanding what guards a branch or what validates input. "
        "Accepts a hex address or exported symbol name."
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path":   ToolProperty(type="string", description="Path to the binary"),
                "target": ToolProperty(type="string", description="Hex address or symbol name to reach"),
            },
            required=["path", "target"],
        )

    def execute(self, tool_input: dict) -> str:
        if not angr_available():
            return "Error: angr not installed. Run: pip install angr"

        binary  = tool_input["path"]
        target  = tool_input["target"]
        timeout = scaled_timeout(config.tools.angr.timeout_constraints, binary)

        out = run_angr_script(_TEMPLATE, timeout, {
            "ANGR_BINARY": os.path.abspath(binary),
            "ANGR_TARGET": target,
        })

        if not out["ok"]:
            return f"Error: {out['error']}"

        r = out["result"]
        if not r["reachable"]:
            return f"NOT REACHABLE: cannot reach '{target}' — no constraints to report"

        constraints = r["constraints"]
        if not constraints:
            return f"REACHABLE: '{target}' reached with no symbolic constraints (unconditional path)"

        lines = [
            f"REACHABLE: '{target}' requires {r['constraint_count']} constraint(s):",
            "",
        ]
        for i, c in enumerate(constraints[:50], 1):
            lines.append(f"  [{i:3}] {c}")
        if len(constraints) > 50:
            lines.append(f"  ... ({len(constraints) - 50} more constraints truncated)")
        return "\n".join(lines)
