"""angr_explore — open-ended symbolic execution via LLM-generated scripts."""
from __future__ import annotations
import os
import tempfile
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.symbolic.angr_runner import angr_available, scaled_timeout, run_angr_script
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

_SYSTEM = """\
You are an expert in angr symbolic execution. Write a self-contained Python script that:
1. Imports angr, claripy, json, os
2. Reads ANGR_BINARY and ANGR_OUTPUT from os.environ
3. Performs symbolic execution to answer the user's goal
4. Writes a JSON dict to ANGR_OUTPUT with keys: ok (bool), result (any), error (str or null)

Rules:
- Use auto_load_libs=False
- Handle exceptions and write {"ok": false, "result": null, "error": "..."} on failure
- Keep the script under 60 lines
- Do NOT use plt.show() or any display calls
- Output ONLY the raw Python script, no markdown fences
"""


def _generate_script(binary: str, goal: str) -> str:
    from providers.factory import get_runtime_provider
    provider = get_runtime_provider()
    prompt = (
        f"Binary: {binary}\n"
        f"Goal: {goal}\n\n"
        "Write the angr script now."
    )
    response = provider.chat(
        messages=[{"role": "user", "content": prompt}],
        tools=[],
        system=_SYSTEM,
    )
    code = ""
    for block in response.content:
        if hasattr(block, "text"):
            code += block.text
    # Strip markdown fences if the model added them
    lines = code.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


class AngrExploreTool(BaseTool):
    name = "angr_explore"
    description = (
        "Open-ended symbolic execution: describe a goal in natural language and angr "
        "will try to achieve it. Examples: 'find input that prints the flag', "
        "'prove this buffer can exceed 256 bytes', 'find argv that avoids the error branch'. "
        "Generates and runs a custom angr script. Timeout scales with binary complexity."
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary"),
                "goal": ToolProperty(
                    type="string",
                    description="Natural-language description of what to find or prove",
                ),
            },
            required=["path", "goal"],
        )

    def execute(self, tool_input: dict) -> str:
        if not angr_available():
            return "Error: angr not installed. Run: pip install angr"

        binary = tool_input["path"]
        goal   = tool_input["goal"]

        logger.info(f"  angr_explore: generating script for goal: {goal!r}")
        try:
            script_code = _generate_script(binary, goal)
        except Exception as e:
            return f"Error generating angr script: {e}"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, prefix="angr_explore_"
        ) as tmp:
            tmp.write(script_code)
            script_path = tmp.name

        logger.info(f"  angr_explore: running generated script ({len(script_code)} chars)")
        timeout = scaled_timeout(config.tools.angr.timeout_explore, binary)
        try:
            out = run_angr_script(script_path, timeout, {
                "ANGR_BINARY": os.path.abspath(binary),
            })
        finally:
            os.unlink(script_path)

        if not out["ok"]:
            return (
                f"Symbolic exploration failed: {out['error']}\n\n"
                f"Generated script:\n```python\n{script_code}\n```"
            )

        result = out.get("result")
        if result is None:
            return "(exploration completed but returned no result)"

        import json
        return (
            f"Symbolic exploration result:\n"
            f"{json.dumps(result, indent=2)}"
        )
