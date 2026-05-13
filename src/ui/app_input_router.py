"""Input router for the arc-tui — dispatches raw text to the right handler.

Handles the ordering: session picker → slash command → escalation →
ASK_USER → queue (if busy) → normal send.
"""
from __future__ import annotations

from ui.conversation import ConversationModel
from ui.spinner_model import SpinnerModel
from ui.input_model import InputModel
from service import AgentService


async def handle_input(
    text: str,
    conv: ConversationModel,
    spinner: SpinnerModel,
    input_model: InputModel,
    service: AgentService,
    app_state: dict,
) -> None:
    app = app_state.get("app")

    # Session picker mode: the user is choosing which session to resume.
    # This MUST be checked before slash commands so that `/exit` etc. don't
    # accidentally get interpreted during picker mode (rare but possible).
    if input_model.pending_session_options is not None:
        from ui.app_resume import handle_resume_selection
        await handle_resume_selection(text, service, conv, input_model)
        if app:
            app.invalidate()
        return

    # Slash command
    if text.startswith("/"):
        from ui.app_commands import execute_command
        parts = text[1:].split(maxsplit=1)
        name = parts[0].lower()
        cmd_args = parts[1] if len(parts) > 1 else ""
        await execute_command(name, cmd_args, conv, spinner, input_model, service, app_state)
        if app:
            app.invalidate()
        return

    # Escalation response — route y/n to the gate, not the service
    gate = input_model.escalation_gate
    if gate and gate.pending_escalation:
        if text.lower() in ("y", "yes"):
            gate.supply_answer(True)
            conv.add("ansigreen", "✓  Allowed.\n\n")
        elif text.lower() in ("n", "no"):
            gate.supply_answer(False)
            conv.add("ansired", "✗  Denied.\n\n")
        else:
            conv.add("ansiyellow", "Type  y  to allow or  n  to deny\n")
        if app:
            app.invalidate()
        return

    # ASK_USER clarification response — route to TUIInputGate
    igate = getattr(input_model, "input_gate", None)
    if igate and igate.pending_question:
        igate.supply_answer(text)
        conv.add("ansigray", "✓  Clarification provided.\n\n")
        if app:
            app.invalidate()
        return

    # Queue if agent is busy with another turn
    if service.is_busy:
        input_model.queue_message(text)
        conv.add("ansigray", "(queued — will send after current turn)\n")
        if app:
            app.invalidate()
        return

    # Normal message — add user bubble then dispatch to service
    conv.add_user_message(text)
    if app:
        app.invalidate()
    await service.send(text)
