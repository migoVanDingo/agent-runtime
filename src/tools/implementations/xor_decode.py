from tools.base import BaseTool, InputSchema, ToolProperty


class XorDecodeTool(BaseTool):
    name = "xor_decode"
    description = "XOR a hex-encoded byte string against a key. Key can be a single byte or multi-byte (repeating). Returns hex and attempted UTF-8 decode."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "data": ToolProperty(type="string", description="Hex-encoded data to XOR (e.g. 'deadbeef')"),
                "key": ToolProperty(type="string", description="Hex-encoded key (e.g. 'ff' for single byte, 'aabbcc' for multi-byte repeating)"),
            },
            required=["data", "key"],
        )

    def execute(self, tool_input: dict) -> str:
        try:
            data = bytes.fromhex(tool_input["data"])
            key = bytes.fromhex(tool_input["key"])
            result = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
            hex_result = result.hex()
            try:
                text_result = result.decode("utf-8")
            except UnicodeDecodeError:
                text_result = "(not valid UTF-8)"
            return f"hex: {hex_result}\ntext: {text_result}"
        except ValueError as e:
            return f"Invalid hex input: {e}"
        except Exception as e:
            return f"Error: {e}"
