import hashlib
from tools.base import BaseTool, InputSchema, ToolProperty


class HashFileTool(BaseTool):
    name = "hash_file"
    description = "Compute the hash of a file. Supports MD5, SHA1, SHA256, SHA512."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the file"),
                "algorithm": ToolProperty(type="string", description="Hash algorithm: 'md5', 'sha1', 'sha256', 'sha512'. Defaults to 'sha256'."),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        algorithm = tool_input.get("algorithm", "sha256").lower()

        supported = {"md5", "sha1", "sha256", "sha512"}
        if algorithm not in supported:
            return f"Unsupported algorithm '{algorithm}'. Choose from: {', '.join(sorted(supported))}"

        try:
            h = hashlib.new(algorithm)
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return f"{algorithm}: {h.hexdigest()}  {path}"
        except FileNotFoundError:
            return f"File not found: {path}"
        except Exception as e:
            return f"Error: {e}"
