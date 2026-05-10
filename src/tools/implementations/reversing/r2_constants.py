import json
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.r2_runner import r2_run


class R2ConstantsTool(BaseTool):
    name = "r2_constants"
    description = (
        "Extract strings with their addresses and data references from a binary. "
        "Combines string table (iz) with value analysis (aav) to surface constants, "
        "magic numbers, and embedded data. Does not require full analysis for strings."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary"),
                "min_length": ToolProperty(
                    type="string",
                    description="Minimum string length (default: 4)",
                ),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        min_len = int(tool_input.get("min_length", "4"))

        # Strings with addresses (no full analysis needed)
        raw = r2_run(path, f"e str.min_length={min_len}; izj", analyze=False)
        if raw.startswith("Error:"):
            return raw

        # Parse JSON — izj may return just the array
        # Strip leading config echo if present
        json_start = raw.find("[")
        strings_out = ""
        if json_start >= 0:
            try:
                strings = json.loads(raw[json_start:])
                if strings:
                    lines = [f"{'Address':<18}  {'Length':>6}  Value"]
                    lines.append("-" * 60)
                    for s in strings:
                        addr = f"0x{s.get('vaddr', 0):016x}"
                        length = s.get("length", 0)
                        val = s.get("string", "")
                        lines.append(f"{addr}  {length:>6}  {val!r}")
                    strings_out = "\n".join(lines) + f"\n\n{len(strings)} string(s) found"
                else:
                    strings_out = "(no strings found)"
            except json.JSONDecodeError:
                strings_out = r2_run(path, f"e str.min_length={min_len}; iz", analyze=False)
        else:
            strings_out = raw or "(no strings found)"

        return strings_out
