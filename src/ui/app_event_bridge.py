"""Background asyncio tasks that bridge service events to UI models.

Provides three coroutines meant to run as concurrent tasks during the TUI:
- consume_events: drain service.events() and update conv/spinner
- spinner_tick: advance spinner animation frame every 0.4s
- escalation_watcher: detect escalation/ASK_USER and inject prompts
"""
from __future__ import annotations

import asyncio

from ui.conversation import ConversationModel
from ui.spinner_model import SpinnerModel
from ui.input_model import InputModel
from service import AgentService

_STAGE_LABELS = {
    "RoutingStage":         "Routing",
    "PlanningStage":        "Planning",
    "ExecutionStage":       "Executing",
    "SynthesizerStage":     "Synthesizing",
    "DirectExecutionStage": "Working",
    "CouncilStage":         "Reviewing",
    "ContinuationStage":    "Evaluating",
    "RagContextStage":      "Memory",
    "EntityCriticStage":    "Entities",
    "ValidatorStage":       "Validating",
    "SkillHintStage":       "Skills",
}


async def consume_events(
    service: AgentService,
    conv: ConversationModel,
    spinner: SpinnerModel,
    input_model: InputModel,
    app_state: dict,
) -> None:
    """Drain service.events() and update conversation/spinner models accordingly.

    Escalation detection is handled exclusively by escalation_watcher —
    this coroutine only processes AgentEvents.
    """
    streaming = False

    async for event in service.events():
        app = app_state.get("app")
        t = event.type

        if t == "turn.started":
            streaming = False
            spinner.start("Thinking")

        elif t == "stage.started":
            raw = getattr(event, "stage", "")
            label = _STAGE_LABELS.get(raw, raw.replace("Stage", "") or "Working")
            spinner.update(label)

        elif t == "tool.call.started":
            tool = getattr(event, "tool_name", "tool")
            spinner.update(tool)

        elif t == "content.token_chunk":
            if not streaming:
                spinner.stop()
                conv.begin_agent_response()
                streaming = True
            conv.append_token(getattr(event, "text", ""))

        elif t == "content.message_complete":
            if not streaming:
                spinner.stop()
                conv.begin_agent_response()
            conv.finalize_agent_response(getattr(event, "text", ""))
            streaming = False

        elif t == "turn.completed":
            spinner.stop()
            ms = getattr(event, "elapsed_ms", 0)
            tokens_in = getattr(event, "tokens_in", 0)
            tokens_out = getattr(event, "tokens_out", 0)
            # Accumulate session totals so the footer can show cumulative usage.
            input_model.total_tokens_in += tokens_in
            input_model.total_tokens_out += tokens_out
            if ms or tokens_in or tokens_out:
                conv.add_timer(ms, tokens_in, tokens_out)
            else:
                conv.add("", "\n")
            streaming = False
            # Drain one queued message now that the turn is done
            nxt = input_model.pop_pending()
            if nxt:
                conv.add_user_message(nxt)
                if app:
                    app.invalidate()
                await service.send(nxt)

        elif t == "turn.failed":
            spinner.stop()
            conv.add_error(getattr(event, "error", "unknown error"))
            streaming = False

        elif t == "turn.cancelled":
            spinner.stop()
            conv.add_cancelled()
            streaming = False

        if app:
            app.invalidate()


async def spinner_tick(spinner: SpinnerModel, app_state: dict) -> None:
    """Advance the spinner animation frame every 0.4 s and trigger a redraw."""
    while True:
        if spinner.active:
            spinner.tick()
            app = app_state.get("app")
            if app:
                app.invalidate()
        await asyncio.sleep(0.4)


async def escalation_watcher(
    input_model: InputModel,
    conv: ConversationModel,
    app_state: dict,
) -> None:
    """Watch for pending escalations and ASK_USER questions; inject into conversation.

    Runs independently of consume_events so display is not coupled
    to the event stream cadence.
    """
    shown_esc = None
    shown_q = None
    while True:
        app = app_state.get("app")

        # Escalation gate
        gate = input_model.escalation_gate
        if gate:
            esc = gate.pending_escalation
            if esc is not None and esc is not shown_esc:
                shown_esc = esc
                conv.add_escalation(esc)
                if app:
                    app.invalidate()
            elif esc is None and shown_esc is not None:
                shown_esc = None

        # ASK_USER input gate
        igate = getattr(input_model, "input_gate", None)
        if igate:
            q = igate.pending_question
            if q is not None and q is not shown_q:
                shown_q = q
                conv.add("ansiyellow bold", f"\n❓  {q}\n")
                conv.add("ansigray", "  (Type your clarification and press Enter)\n\n")
                if app:
                    app.invalidate()
            elif q is None and shown_q is not None:
                shown_q = None

        await asyncio.sleep(0.1)
