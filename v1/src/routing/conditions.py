import re
from typing import Callable

_FILE_PATH_PATTERN = re.compile(
    r'[~/.]?(?:/[\w.\-]+)+'
    r'|[\w.\-]+\.(?:py|txt|md|json|yaml|yml|csv|log|sh|c|cpp|h|rs|go|js|ts|o|so|a|dylib|elf|bin|exe|out)'
)


def has_extension(*extensions: str) -> Callable:
    def condition(message: str, _: list[dict]) -> bool:
        return any(
            word.rstrip(".,;:\"'").lower().endswith(ext)
            for word in message.split()
            for ext in extensions
        )
    return condition


def has_file_path() -> Callable:
    def condition(message: str, _: list[dict]) -> bool:
        return bool(_FILE_PATH_PATTERN.search(message))
    return condition


def any_keyword(*keywords: str) -> Callable:
    kw_set = {k.lower() for k in keywords}
    def condition(message: str, _: list[dict]) -> bool:
        tokens = set(re.findall(r'\b\w+\b', message.lower()))
        return bool(tokens & kw_set)
    return condition


def last_tools_were(*tool_names: str) -> Callable:
    names = set(tool_names)
    def condition(_: str, history: list[dict]) -> bool:
        for msg in reversed(history):
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    calls = [
                        b.get("name") for b in content
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    ]
                    if calls:
                        return all(c in names for c in calls)
        return False
    return condition


def all_of(*conditions: Callable) -> Callable:
    def condition(message: str, history: list[dict]) -> bool:
        return all(c(message, history) for c in conditions)
    return condition
