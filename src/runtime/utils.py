"""Shared runtime utilities used by pipeline stages and agent.py.

These were previously defined as module-level functions in agent.py.
Extracted here so stage files can import them without circular dependencies.
"""
from __future__ import annotations
import re
import json
from runtime.schema import ClassifierResult
from runtime.prompts import ROUTING_HEADER_INSTRUCTIONS

_W = 56  # log banner width

# Same logic as monitor._TOOL_ERROR_RE — match tool failure formats, not content.
_ERROR_INDICATORS = re.compile(
    r"(?im)("
    r"^Error[:\s]|"
    r"^STDERR:|"
    r"^File not found|"
    r"^Tool call (?:blocked|denied)|"
    r"command not found|"
    r"cannot open|"                   # file_info: "cannot open `path' (No such file or directory)"
    r"No such file or directory|"    # shell / file_info failures
    r"Traceback \(most recent call last\)|"
    r"I don't have|I cannot|I'm unable"
    r")"
)

_ROUTE_RE = re.compile(r"<route>(.*?)</route>", re.DOTALL)

# Phrases that indicate the model is promising work it cannot do without tools,
# OR falsely claiming it lacks capabilities it actually has (web tools, etc.).
_ACTION_PHRASES = (
    "let me ", "i'll ", "i will ", "i'm going to ", "let's ", "allow me",
    "i don't have direct internet", "i don't have access to the internet",
    "i cannot access the internet", "i can't access the internet",
    "i don't have real-time", "i lack internet", "i'm unable to browse",
    "i cannot browse", "i can't browse",
    "i cannot search the web", "i can't search the web",
    "i don't have search", "i cannot perform web searches",
    "i can't read pdf", "i cannot read pdf", "i cannot open pdf",
    "i don't have access to git", "i cannot run git",
)


def has_error_indicator(text: str) -> bool:
    """Return True if text contains a tool-level error marker."""
    return bool(_ERROR_INDICATORS.search(text[:500]))


def banner(text: str) -> str:
    """Format a section banner for the session log."""
    prefix = f"── {text} "
    return prefix + "─" * max(0, _W - len(prefix))


def fmt_input(name: str, tool_input: dict) -> str:
    """Format a tool call input for compact log display."""
    if name == "write_file":
        size = len(tool_input.get("content", ""))
        return f"{tool_input.get('path', '?')}  ({size} chars)"
    if "path" in tool_input:
        extras = {k: v for k, v in tool_input.items() if k != "path"}
        suffix = f"  {extras}" if extras else ""
        return f"{tool_input['path']}{suffix}"
    if "command" in tool_input:
        return tool_input["command"]
    return str(tool_input)


def fmt_result(result: str) -> str:
    """Format a tool result for compact log display."""
    stripped = result.strip()
    if not stripped:
        return "(empty)"
    lines = stripped.splitlines()
    if len(lines) == 1:
        return lines[0]
    return f"{lines[0]}  … ({len(lines)} lines)"


def build_routing_system(base_system: str, wf_descriptions: list[tuple[str, str]]) -> str:
    """Prepend routing header instructions to the agent system prompt."""
    wf_lines = "\n".join(f'  "{name}": {intent}' for name, intent in wf_descriptions) or "  (none)"
    return base_system + "\n\n" + ROUTING_HEADER_INSTRUCTIONS.format(workflow_descriptions=wf_lines)


def parse_routing_response(
    text: str,
    valid_workflows: set[str] | None = None,
) -> tuple[ClassifierResult, str]:
    """Extract <route>...</route> header from model response.

    Returns (ClassifierResult, remaining_text). On any parse failure
    defaults to direct/low so the agent always makes forward progress.
    """
    m = _ROUTE_RE.search(text)
    if not m:
        return ClassifierResult(mode="plan", risk="low"), text

    remaining = text[m.end():].strip()
    from runtime.json_extract import extract_json
    data = extract_json(m.group(1).strip())
    if not isinstance(data, dict):
        return ClassifierResult(mode="plan", risk="low"), remaining

    mode = data.get("mode", "direct")
    risk = data.get("risk", "low")
    skill_hint = data.get("skill") or data.get("workflow") or None

    if mode not in ("plan", "direct"):
        mode = "direct"
    if risk not in ("low", "moderate", "high"):
        risk = "low"
    if skill_hint and valid_workflows and skill_hint not in valid_workflows:
        skill_hint = None

    return ClassifierResult(mode=mode, risk=risk, skill_hint=skill_hint), remaining


def is_clean_inline_answer(text: str) -> bool:
    """Return True if text is a genuine conversational inline answer.

    Returns False if the text contains code fences (model showing commands
    it cannot run) or action-promising phrases (model saying it will do
    work it cannot do without tools).
    """
    if not text:
        return False
    if "```" in text:
        return False
    lower = text.lower()
    if any(p in lower for p in _ACTION_PHRASES):
        return False
    return True


def extract_entity_context(packed_messages: list[dict]) -> str | None:
    """Build entity critic context from packed messages.

    Includes only user text messages and assistant text — not tool results.
    Tool results contain command output, binary strings, etc. which produce
    false-positive path candidates for the entity critic.
    """
    from runtime.context_manager import _message_text

    def _is_tool_result_msg(m: dict) -> bool:
        content = m.get("content", "")
        return (
            m.get("role") == "user"
            and isinstance(content, list)
            and any(b.get("type") == "tool_result" for b in content)
        )

    lines = [
        _message_text(m)
        for m in packed_messages
        if not _is_tool_result_msg(m)
    ]
    text = "\n".join(lines).strip()
    return text if text else None
