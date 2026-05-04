"""Log formatting helpers: ANSI colour palette, tag functions.

Extracted from logger.py so the logger module stays focused on
configuration and handler setup.
"""
from __future__ import annotations

import os
import sys

_RESET = "\033[0m"
_BOLD  = "\033[1m"

_COLOR_COUNCIL   = "\033[93m"
_COLOR_SYNTHESIS = "\033[1m"
_COLOR_ESCALATE  = "\033[33m"
_COLOR_USER      = "\033[96m"
_COLOR_ASSISTANT = "\033[92m"

_COUNCILLOR_PALETTE = [
    "\033[34m",  # blue
    "\033[35m",  # magenta
    "\033[33m",  # yellow
    "\033[36m",  # cyan
    "\033[32m",  # green
]


class LogFormatting:
    """Per-process colour palette state for council log tags.

    Keeping this in a class (rather than module globals) makes it easier
    to reset in tests.
    """

    def __init__(self) -> None:
        self._councillor_color_map: dict[str, str] = {}

    def get_councillor_color(self, label: str) -> str:
        if not _is_tty():
            return ""
        if label not in self._councillor_color_map:
            idx = len(self._councillor_color_map) % len(_COUNCILLOR_PALETTE)
            self._councillor_color_map[label] = _COUNCILLOR_PALETTE[idx]
        return self._councillor_color_map[label]

    def council_tag(self, label: str) -> str:
        if not _is_tty():
            return f"[council][{label}]"
        color = self.get_councillor_color(label)
        return f"{_COLOR_COUNCIL}[council]{_RESET}{color}[{label}]{_RESET}"

    def council_header_tag(self) -> str:
        if not _is_tty():
            return "[council]"
        return f"{_COLOR_COUNCIL}[council]{_RESET}"

    def synth_tag(self) -> str:
        if not _is_tty():
            return "[synth]"
        return f"{_COLOR_SYNTHESIS}[synth]{_RESET}"

    def user_tag(self) -> str:
        if not _is_tty():
            return "[user]"
        return f"{_COLOR_USER}[user]{_RESET}"

    def assistant_tag(self) -> str:
        if not _is_tty():
            return "[assistant]"
        return f"{_COLOR_ASSISTANT}[assistant]{_RESET}"

    def escalate_tag(self) -> str:
        if not _is_tty():
            return "[escalate]"
        return f"{_COLOR_ESCALATE}[escalate]{_RESET}"


def _is_tty() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


# Process-level default instance
_default = LogFormatting()


def get_councillor_color(label: str) -> str:
    return _default.get_councillor_color(label)


def council_tag(label: str) -> str:
    return _default.council_tag(label)


def council_header_tag() -> str:
    return _default.council_header_tag()


def synth_tag() -> str:
    return _default.synth_tag()


def user_tag() -> str:
    return _default.user_tag()


def assistant_tag() -> str:
    return _default.assistant_tag()


def escalate_tag() -> str:
    return _default.escalate_tag()
