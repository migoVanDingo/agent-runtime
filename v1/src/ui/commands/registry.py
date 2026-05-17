"""Slash command registry for arc-tui.

Commands are registered as async callables with signature:
    async def handler(app: ArcApp, args: str) -> None

The registry is keyed by command name (without leading slash).
Aliases map to the same Command object.

Exported: Command, CommandRegistry
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from ui.app import ArcApp


@dataclass
class Command:
    """A registered slash command with metadata and handler."""
    name: str
    description: str
    handler: Callable[["ArcApp", str], Awaitable[None]]
    aliases: list[str] = field(default_factory=list)
    usage: str = ""


class CommandRegistry:
    """Maps slash command names to Command objects.

    Usage:
        registry = CommandRegistry()
        registry.register(Command("exit", "Exit arc-tui", _exit_handler))
        cmd = registry.get("exit")
        matches = registry.completions_for("/ex")
    """

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, cmd: Command) -> None:
        """Register a command and all its aliases."""
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd

    def get(self, name: str) -> Command | None:
        """Look up by name (with or without leading slash)."""
        return self._commands.get(name.lstrip("/"))

    def completions_for(self, prefix: str) -> list[Command]:
        """Return commands whose name starts with prefix (after stripping /).

        Returns unique commands (aliases do not produce duplicate entries).
        Sorted alphabetically by primary command name.
        """
        clean = prefix.lstrip("/").lower()
        seen: set[str] = set()
        results: list[Command] = []
        for name, cmd in self._commands.items():
            if name.startswith(clean) and cmd.name not in seen:
                seen.add(cmd.name)
                results.append(cmd)
        return sorted(results, key=lambda c: c.name)

    def all_commands(self) -> list[Command]:
        """Return all unique registered commands (no alias duplicates), sorted."""
        seen: set[str] = set()
        result: list[Command] = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return sorted(result, key=lambda c: c.name)
