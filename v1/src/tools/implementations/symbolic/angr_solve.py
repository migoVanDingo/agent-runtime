import os
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.symbolic.angr_runner import angr_available, scaled_timeout, run_angr_script
from app_config import config

_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "solve_input.py")


class AngrSolveTool(BaseTool):
    name = "angr_solve"
    description = (
        "Find stdin or argv input that causes execution to reach a success address "
        "while avoiding failure addresses. "
        "Solves passwords, keys, checksums, and CTF crackme challenges. "
        "Accepts hex addresses or exported symbol names. "
        "Timeout scales with binary complexity."
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path":       ToolProperty(type="string", description="Path to the binary"),
                "find":       ToolProperty(type="string", description="Hex address or symbol to reach (the 'success' state)"),
                "avoid":      ToolProperty(type="string", description="Comma-separated hex addresses to avoid (e.g. error/failure paths)"),
                "input_type": ToolProperty(type="string", description="'stdin' or 'argv' (default: stdin)"),
                "input_len":  ToolProperty(type="string", description="Max symbolic input length in bytes (default: 64)"),
            },
            required=["path", "find"],
        )

    def execute(self, tool_input: dict) -> str:
        if not angr_available():
            return "Error: angr not installed. Run: pip install angr"

        binary     = tool_input["path"]
        find_addr  = tool_input["find"]
        avoid      = tool_input.get("avoid", "")
        input_type = tool_input.get("input_type", "stdin")
        input_len  = tool_input.get("input_len", "64")
        timeout    = scaled_timeout(config.tools.angr.timeout_solve, binary)

        out = run_angr_script(_TEMPLATE, timeout, {
            "ANGR_BINARY":     os.path.abspath(binary),
            "ANGR_FIND":       find_addr,
            "ANGR_AVOID":      avoid,
            "ANGR_INPUT_TYPE": input_type,
            "ANGR_INPUT_LEN":  str(input_len),
        })

        if not out["ok"]:
            return f"Error: {out['error']}"

        r = out["result"]
        if r["solved"]:
            lines = [
                f"SOLVED: input found that reaches '{find_addr}'",
                f"  Input ({input_type}): {r['input']!r}",
                f"  Paths found: {r['paths_found']}",
            ]
            if r.get("avoided"):
                lines.append(f"  Avoided: {r['avoided']}")
            return "\n".join(lines)
        lines = [f"UNSOLVED: no input found that reaches '{find_addr}'"]
        if r.get("avoided"):
            lines.append(f"  Avoided: {r['avoided']}")
        return "\n".join(lines)
