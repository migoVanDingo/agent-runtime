import base64
from tools.base import BaseTool, InputSchema, ToolProperty


class Base64EncodeTool(BaseTool):
    name = "base64_encode"
    description = "Base64-encode a string or hex bytes."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "input": ToolProperty(type="string", description="The string to encode"),
                "input_format": ToolProperty(type="string", description="'text' (default) or 'hex' if input is a hex string"),
            },
            required=["input"],
        )

    def execute(self, tool_input: dict) -> str:
        raw = tool_input["input"]
        input_format = tool_input.get("input_format", "text")
        try:
            data = bytes.fromhex(raw) if input_format == "hex" else raw.encode("utf-8")
            return base64.b64encode(data).decode("utf-8")
        except Exception as e:
            return f"Error: {e}"


class Base64DecodeTool(BaseTool):
    name = "base64_decode"
    description = "Decode a base64-encoded string. Returns decoded text or hex if output is not valid UTF-8."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "input": ToolProperty(type="string", description="The base64-encoded string to decode"),
            },
            required=["input"],
        )

    def execute(self, tool_input: dict) -> str:
        raw = tool_input["input"]
        try:
            decoded = base64.b64decode(raw)
            try:
                return decoded.decode("utf-8")
            except UnicodeDecodeError:
                return f"(binary) hex: {decoded.hex()}"
        except Exception as e:
            return f"Error: {e}"
