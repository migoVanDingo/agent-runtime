import os
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.symbolic.angr_runner import angr_available, scaled_timeout, run_angr_script
from app_config import config

_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "reachable.py")


class AngrReachableTool(BaseTool):
    name = "angr_reachable"
    description = (
        "Symbolically check whether execution can reach a target address or function. "
        "Returns reachable (true/false) and path count. "
        "Accepts a hex address (e.g. '0x401234') or exported symbol name. "
        "Optional: comma-separated addresses to avoid. "
        "Timeout scales with binary complexity (function count)."
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path":   ToolProperty(type="string", description="Path to the binary"),
                "target": ToolProperty(type="string", description="Hex address or symbol name to reach"),
                "avoid":  ToolProperty(type="string", description="Comma-separated hex addresses to avoid (optional)"),
            },
            required=["path", "target"],
        )

    def execute(self, tool_input: dict) -> str:
        if not angr_available():
            return "Error: angr not installed. Run: pip install angr"

        binary  = tool_input["path"]
        target  = tool_input["target"]
        avoid   = tool_input.get("avoid", "")
        timeout = scaled_timeout(config.tools.angr.timeout_reachable, binary)

        out = run_angr_script(_TEMPLATE, timeout, {
            "ANGR_BINARY": os.path.abspath(binary),
            "ANGR_TARGET": target,
            "ANGR_AVOID":  avoid,
        })

        if not out["ok"]:
            return f"Error: {out['error']}"

        r = out["result"]
        if r["reachable"]:
            lines = [f"REACHABLE: execution can reach '{target}'",
                     f"  Paths found: {r['path_count']}"]
            if r.get("avoided"):
                lines.append(f"  Avoided: {r['avoided']}")
            return "\n".join(lines)
        lines = [f"NOT REACHABLE: no path found to '{target}'"]
        if r.get("avoided"):
            lines.append(f"  Avoided: {r['avoided']}")
        return "\n".join(lines)
