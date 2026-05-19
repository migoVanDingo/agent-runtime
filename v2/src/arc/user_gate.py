"""UserGate — the runtime's bridge to ask the user yes/no questions mid-turn.

Used by the guard plugin for escalation prompts. Could be used by other
plugins later (e.g., destructive-operation confirms).

Three implementations:
  - UserGate (Protocol)    contract
  - NoOpGate               auto-denies; for headless `arc run` and scripts
  - TUIGate                prompt_toolkit-based y/n; for interactive `arc`

Tests inject fakes that record what was asked and return a scripted answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EscalationRequest:
    """What the guard hands to the gate when a tool needs approval."""
    tool_name: str
    command: str  # the actual command/input that tripped the policy
    reason: str   # human-readable: why are we asking


class UserGate(Protocol):
    """Protocol every gate implements. Sync — runs inline during a turn."""

    def prompt_for_escalation(self, request: EscalationRequest) -> bool:
        """Return True to allow, False to deny. Should not raise."""
        ...


class NoOpGate:
    """Always denies. Used in headless mode where there's no human to ask.

    Writes a single line to stderr so the user knows why a tool was denied
    when reviewing `arc run` output.
    """

    def __init__(self, *, verbose: bool = True) -> None:
        self._verbose = verbose

    def prompt_for_escalation(self, request: EscalationRequest) -> bool:
        if self._verbose:
            import sys
            print(
                f"[guard] denied (no interactive user): "
                f"{request.tool_name} — {request.reason}",
                file=sys.stderr,
            )
        return False


class TUIGate:
    """Prompts the user via prompt_toolkit. For interactive `arc`.

    Uses a fresh prompt invocation each time (not a long-lived session)
    because escalations are rare and we don't need history/completion here.
    Wraps in `patch_stdout(raw=True)` so the prompt doesn't fight with
    whatever else is rendering.
    """

    def __init__(self, console) -> None:
        self._console = console

    def prompt_for_escalation(self, request: EscalationRequest) -> bool:
        self._console.print()
        self._console.print(f"[yellow]⚠ guard escalation:[/yellow] {request.reason}")
        self._console.print(f"  tool:    [bold]{request.tool_name}[/bold]")
        if request.command:
            preview = request.command
            if len(preview) > 200:
                preview = preview[:200] + "..."
            self._console.print(f"  command: [yellow]{preview}[/yellow]")

        try:
            from prompt_toolkit import prompt as pt_prompt
            from prompt_toolkit.patch_stdout import patch_stdout
            with patch_stdout(raw=True):
                answer = pt_prompt("approve? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            self._console.print("[red]escalation cancelled — denied[/red]")
            return False
        except Exception as e:
            self._console.print(f"[red]escalation prompt failed: {e} — denied[/red]")
            return False

        approved = answer in ("y", "yes")
        if approved:
            self._console.print("[green]approved[/green]")
        else:
            self._console.print("[red]denied[/red]")
        return approved
