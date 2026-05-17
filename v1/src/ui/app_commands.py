"""Slash command dispatcher for the arc-tui conversation interface."""
from __future__ import annotations

from ui.conversation import ConversationModel
from ui.spinner_model import SpinnerModel
from ui.input_model import InputModel
from service import AgentService


async def execute_command(
    name: str,
    args: str,
    conv: ConversationModel,
    spinner: SpinnerModel,
    input_model: InputModel,
    service: AgentService,
    app_state: dict,
) -> None:
    """Handle slash commands directly in the conversation model."""
    if name in ("exit", "quit"):
        app = app_state.get("app")
        if app:
            app.exit()

    elif name == "help":
        conv.add("bold", "\nCommands\n")
        rows = [
            ("/exit, /quit", "End the session"),
            ("/pause",       "Pause the running agent (also ESC)"),
            ("/resume",      "Unpause paused agent OR pick a session to restore"),
            ("/sessions",    "Pick a prior session to restore"),
            ("/cancel",      "Cancel the current turn"),
            ("/clear",       "Clear the screen"),
            ("/settings",    "Show current settings"),
            ("/help",        "Show this help"),
        ]
        for cmd, desc in rows:
            conv.add("ansicyan", f"  {cmd:<20}")
            conv.add("ansigray", f"  {desc}\n")
        conv.add("", "\n")

    elif name == "pause":
        await service.pause()
        app_state["paused"] = True
        conv.add("ansiyellow", "Paused.  /resume or ESC to continue.\n")

    elif name == "resume":
        # Smart routing: if the agent is paused mid-turn, /resume unpauses.
        # Otherwise treat /resume as "show me past sessions to restore."
        if app_state.get("paused"):
            await service.resume()
            app_state["paused"] = False
            conv.add("ansigreen", "Resumed.\n")
        else:
            from ui.app_resume import handle_resume
            await handle_resume(service, conv, input_model, app_state)

    elif name == "sessions":
        # Alias for `/resume` — shows the resumable session picker.
        from ui.app_resume import handle_resume
        await handle_resume(service, conv, input_model, app_state)

    elif name == "cancel":
        await service.cancel_current_turn()
        conv.add("ansiyellow", "Cancelling…\n")

    elif name == "clear":
        # Reset the conversation buffer without touching the model reference
        conv._chunks.clear()
        conv._cursor_idx = 0
        conv._auto_scroll = True

    elif name == "settings":
        try:
            from ui.settings_store import get_settings_store
            s = get_settings_store().load()
            conv.add("bold", "\nSettings\n")
            for k, v in s.model_dump().items():
                conv.add("ansicyan", f"  {k:<20}")
                conv.add("", f"  {v}\n")
            conv.add("", "\n")
        except Exception as e:
            conv.add("ansired", f"Settings error: {e}\n")

    else:
        conv.add("ansiyellow", f"Unknown command: /{name}  (try /help)\n")
