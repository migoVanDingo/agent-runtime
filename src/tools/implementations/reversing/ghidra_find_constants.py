from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.ghidra_cache import ghidra_home, run_ghidra_function

_MAGIC = {
    2654435769: "TEA DELTA (0x9e3779b9)",
    2654435761: "TEA negative DELTA (0x61c88647)",
    1779033703: "SHA-256 H0 (0x6a09e667)",
    1732584193: "MD5 A (0x67452301)",
    4023233417: "MD5 B (0xefcdab89)",
    2562383102: "SHA-1 K1 (0x5a827999)",
    1518500249: "SHA-1 K2 (0x6ed9eba1)",
}


def _find_constants(api):
    listing = api.currentProgram.getListing()
    items = []
    annotations = []

    for d in listing.getDefinedData(True):
        dt_name = d.getDataType().getName()
        try:
            val = d.getValue()
            val_str = str(val) if val is not None else None
        except Exception:
            val_str = None

        addr = str(d.getAddress())
        label = str(d.getLabel()) if d.getLabel() else ""

        # Check for known magic constants
        try:
            val_int = int(val_str) if val_str else None
            note = _MAGIC.get(val_int, "")
            if note:
                annotations.append(f"  *** {addr}: {val_str} → {note}")
        except (ValueError, TypeError):
            pass

        items.append(f"  {addr:<20} {dt_name:<15} {repr(val_str):<30}  {label}")

    out = f"{'Address':<20} {'Type':<15} {'Value':<30}  Label\n" + "-" * 75 + "\n"
    out += "\n".join(items[:200])
    if len(items) > 200:
        out += f"\n... ({len(items) - 200} more items truncated)"
    if annotations:
        out += "\n\n=== KNOWN CRYPTO CONSTANTS ===\n" + "\n".join(annotations)
    return out


class GhidraFindConstantsTool(BaseTool):
    name = "ghidra_find_constants"
    description = (
        "Find defined data, strings, and magic constants in a binary using Ghidra/PyGhidra. "
        "Annotates known crypto constants (TEA, SHA-256, MD5, etc.) automatically. "
        "Requires GHIDRA_HOME in .env."
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary"),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        if not ghidra_home():
            return "Error: GHIDRA_HOME not set. Add GHIDRA_HOME=/path/to/ghidra to .env"
        return run_ghidra_function(tool_input["path"], _find_constants)
