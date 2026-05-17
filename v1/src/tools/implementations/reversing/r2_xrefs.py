import json
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.r2_runner import r2_run


class R2XrefsTool(BaseTool):
    name = "r2_xrefs"
    description = (
        "Find cross-references to a function or address in a binary. "
        "Shows what code calls or references the target. "
        "Accepts a function name (e.g. 'sym.encrypt') or hex address (e.g. '0x1234')."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary"),
                "target": ToolProperty(
                    type="string",
                    description="Function name or hex address to find references to.",
                ),
            },
            required=["path", "target"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        target = tool_input["target"]

        raw = r2_run(path, f"axtj @ {target}", analyze=True)
        if raw.startswith("Error:"):
            return raw

        try:
            refs = json.loads(raw)
        except json.JSONDecodeError:
            return r2_run(path, f"axt @ {target}", analyze=True)

        if not refs:
            return f"No references found to '{target}'"

        lines = [f"References to {target} ({len(refs)} found):"]
        for ref in refs:
            from_addr = f"0x{ref.get('from', 0):x}"
            ref_type = ref.get("type", "?")
            fcn = ref.get("fcn_name", "")
            opcode = ref.get("opcode", "")
            loc = f"{fcn} @ {from_addr}" if fcn else from_addr
            lines.append(f"  [{ref_type:4}] {loc}  {opcode}")
        return "\n".join(lines)
