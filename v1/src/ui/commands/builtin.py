"""Built-in slash commands for arc-tui.

All output uses print_formatted_text(HTML(...)) via ui.app._p — prompt_toolkit's
own renderer, which works correctly inside patch_stdout().
"""
from __future__ import annotations

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import HTML

from service import AgentService


def _p(html: str) -> None:
    print_formatted_text(HTML(html))


async def handle_command(
    name: str,
    args: str,
    service: AgentService,
    state,  # ui.app._State
) -> None:
    handler = _COMMANDS.get(name)
    if handler is None:
        _p(f"<ansiyellow>Unknown command</ansiyellow>: /{name}  (try /help)")
        return
    await handler(args, service, state)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def _exit(args, service, state):
    raise SystemExit(0)


async def _help(args, service, state):
    rows = [
        ("/exit, /quit",    "End the session"),
        ("/pause",          "Pause the running agent (also ESC)"),
        ("/resume",         "Resume a paused agent (also ESC)"),
        ("/cancel",         "Cancel the current turn"),
        ("/clear",          "Clear the screen"),
        ("/settings",       "Show current settings"),
        ("/help",           "Show this help"),
    ]
    print()
    _p("<b>Commands</b>")
    for cmd, desc in rows:
        _p(f"  <ansicyan>{cmd:<20}</ansicyan> <ansigray>{desc}</ansigray>")
    print()


async def _pause(args, service, state):
    await service.pause()
    state.paused = True
    _p("<ansiyellow>Paused.</ansiyellow>  /resume or ESC to continue.")


async def _resume(args, service, state):
    await service.resume()
    state.paused = False
    _p("<ansigreen>Resumed.</ansigreen>")


async def _cancel(args, service, state):
    await service.cancel_current_turn()
    state.paused = False
    _p("<ansiyellow>Cancelling current turn…</ansiyellow>")


async def _clear(args, service, state):
    import os
    os.system("clear")


async def _settings(args, service, state):
    try:
        from ui.settings_store import get_settings_store
        s = get_settings_store().load()
        print()
        _p("<b>Settings</b>  <ansigray>(~/.arc/settings.yml)</ansigray>")
        for k, v in s.model_dump().items():
            _p(f"  <ansicyan>{k:<20}</ansicyan> {v}")
        print()
    except Exception as e:
        _p(f"<ansired>Settings error:</ansired> {e}")


# ── Registry ──────────────────────────────────────────────────────────────────

_COMMANDS = {
    "exit":     _exit,
    "quit":     _exit,
    "help":     _help,
    "pause":    _pause,
    "resume":   _resume,
    "cancel":   _cancel,
    "clear":    _clear,
    "settings": _settings,
}
