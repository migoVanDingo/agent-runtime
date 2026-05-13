"""Session resume picker for the arc-tui.

Provides handle_resume() which renders a table of resumable sessions and
arms picker mode, and handle_resume_selection() which loads the chosen session.
"""
from __future__ import annotations

from ui.conversation import ConversationModel
from ui.input_model import InputModel
from service import AgentService


async def handle_resume(
    service: AgentService,
    conv: ConversationModel,
    input_model: InputModel,
    app_state: dict,
) -> None:
    """Display a table of resumable sessions and arm the input picker mode.

    After rendering, sets input_model.pending_session_options so that the next
    user submission is interpreted as a session selection rather than a chat
    message. handle_input handles the actual loading.
    """
    try:
        sessions = []
        if hasattr(service, "list_resumable_sessions"):
            sessions = service.list_resumable_sessions(limit=20)
        if not sessions:
            conv.add("ansigray", "\nNo resumable sessions found. Starting fresh.\n\n")
            return

        # ── Render a clean table ──────────────────────────────────────────────
        from datetime import datetime
        conv.add("ansicyan bold", "\nResumable sessions\n")
        conv.add("ansigray", "─" * 76 + "\n")
        # Header
        conv.add("ansigray bold",
                 f"  {'#':<3} {'Session ID':<30} {'Started':<18} {'Preview'}\n")
        conv.add("ansigray", "─" * 76 + "\n")
        for i, s in enumerate(sessions, 1):
            sid = getattr(s, "session_id", "?")
            preview = (getattr(s, "preview", "") or "").strip().replace("\n", " ")
            if len(preview) > 25:
                preview = preview[:22] + "…"
            started = getattr(s, "started_at", 0)
            date_str = (
                datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M")
                if started else "       —        "
            )
            conv.add("ansicyan bold", f"  {i:<3}")
            conv.add("", f" {sid[:28]:<30}")
            conv.add("ansigray", f" {date_str:<18}")
            conv.add("", f" {preview}\n")
        conv.add("ansigray", "─" * 76 + "\n\n")

        # Arm the input picker mode — see handle_input for the routing.
        input_model.pending_session_options = sessions

    except Exception as e:
        conv.add("ansired", f"Resume error: {e}\n\n")


async def handle_resume_selection(
    text: str,
    service: AgentService,
    conv: ConversationModel,
    input_model: InputModel,
) -> None:
    """Handle the user's response to the session picker prompt."""
    sessions = input_model.pending_session_options or []
    # Clear picker mode regardless of outcome.
    input_model.pending_session_options = None

    text = text.strip().lower()
    if text in ("q", "quit", "cancel"):
        conv.add("ansigray", "Cancelled — starting fresh.\n\n")
        return

    # Empty submission defaults to #1 (most recent).
    idx = 0
    if text:
        try:
            idx = int(text) - 1
        except ValueError:
            conv.add("ansiyellow",
                     f"Not a valid number — starting fresh.\n\n")
            return

    if not (0 <= idx < len(sessions)):
        conv.add("ansiyellow",
                 f"#{idx + 1} is out of range (1–{len(sessions)}). Starting fresh.\n\n")
        return

    chosen = sessions[idx]
    sid = chosen.session_id
    conv.add("ansigreen", f"✓  Resuming {sid}\n")

    try:
        messages = service.load_conversation(sid)
        if not messages:
            conv.add("ansigray", "  (no prior messages found)\n\n")
            return
        conv.add("ansigray", f"  Loaded {len(messages)} prior message(s)\n\n")
        # Render the loaded conversation so the user sees the context.
        from rich.console import Console
        from rich.markdown import Markdown
        import io
        for msg in messages[-20:]:  # show last 20 to avoid screen flood
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Anthropic format: list of content blocks; pull text only
                content = "".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
            if not content:
                continue
            if role == "user":
                conv.add_user_message(str(content))
            else:
                conv.add("ansicyan bold", "\nAgent\n")
                conv.add("", str(content) + "\n")
        conv.add("", "\n")
    except Exception as e:
        conv.add("ansired", f"  Failed to load conversation: {e}\n\n")
