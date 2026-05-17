"""Heuristic compression for messages at different fidelity levels."""


def compress_tool_result(content: str, max_chars: int) -> str:
    """Compress a tool result to COMPRESSED fidelity."""
    lines = content.splitlines()
    if len(content) <= max_chars:
        return content

    if len(lines) <= 8:
        return content[:max_chars] + f"\n[... truncated, {len(content)} chars total]"

    # Keep first 5 + last 3 lines
    head = lines[:5]
    tail = lines[-3:]
    omitted = len(lines) - 8
    return "\n".join(head) + f"\n[... {omitted} lines omitted ...]\n" + "\n".join(tail)


def compress_assistant_text(content: str, max_chars: int) -> str:
    """Compress assistant reasoning to COMPRESSED fidelity."""
    if len(content) <= max_chars:
        return content

    sentences = content.replace("\n", " ").split(". ")
    if len(sentences) <= 2:
        return content[:max_chars] + "..."

    first = sentences[0] + "."
    last = sentences[-1].rstrip(".")  + "."
    return f"{first} [...] {last}"


def placeholder_tool_result(tool_name: str, content: str) -> str:
    """PLACEHOLDER stub for a tool result."""
    return f"[tool result: {tool_name} — {len(content)} chars]"


def placeholder_assistant(content: str) -> str:
    """PLACEHOLDER stub for an assistant message."""
    preview = content[:40].replace("\n", " ")
    return f"[assistant response — {preview}...]"


def placeholder_user(content: str, turn_index: int) -> str:
    """PLACEHOLDER stub for a user message."""
    preview = content[:40].replace("\n", " ")
    return f"[user message, turn {turn_index} — {preview}...]"


def summarize_write_file(tool_input: dict) -> str:
    """Replace write_file content with a summary."""
    path = tool_input.get("path", "?")
    size = len(tool_input.get("content", ""))
    return f"[wrote {size} chars to {path}]"
