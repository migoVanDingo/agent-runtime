"""InputModel — shared state for the input area (normal mode vs escalation).

Responsibilities:
  - Tracks the active TUIUserGate for escalation detection
  - Provides dynamic prompt prefix and footer text for FormattedTextControl
  - Manages the pending message queue (messages sent while agent is busy)
"""
from __future__ import annotations


class InputModel:
    def __init__(self):
        self.escalation_gate = None   # TUIUserGate | None
        self.input_gate = None        # TUIInputGate | None (reserved for future use)
        self._pending_messages: list[str] = []
        # Cumulative tokens used in this session — shown in the footer.
        # Updated by the event consumer on every turn.completed event.
        self.total_tokens_in: int = 0
        self.total_tokens_out: int = 0
        # When set, the next user input is interpreted as a session selection
        # (1-N, blank=#1, q=cancel) rather than a normal chat message.
        # Populated by _handle_resume() before the picker prompt is shown.
        self.pending_session_options: list | None = None
        # Current session ID — shown in the footer. Set by _interactive().
        self.session_id: str = ""

    def get_prompt_prefix(self):
        """Dynamic prompt prefix — changes based on active gate / mode."""
        from prompt_toolkit.formatted_text import FormattedText
        # ASK_USER clarification takes priority
        if self.input_gate and self.input_gate.pending_question:
            return FormattedText([("ansiyellow bold", "  Clarify:  ")])
        # Escalation approval
        if self.escalation_gate and self.escalation_gate.pending_escalation:
            return FormattedText([("ansired bold", "  Allow? [y/n]  ")])
        # Session selection picker
        if self.pending_session_options is not None:
            return FormattedText([("ansicyan bold", "  Pick #  ")])
        # Normal input — blue arrow
        return FormattedText([("ansiblue bold", "  ▶  ")])

    def get_footer_text(self):
        """Footer content — changes for escalation and clarification prompts."""
        from prompt_toolkit.formatted_text import FormattedText
        if self.input_gate and self.input_gate.pending_question:
            return FormattedText([("ansiyellow", "  ❓  Clarification needed  —  type your response and press Enter")])
        if self.escalation_gate and self.escalation_gate.pending_escalation:
            return FormattedText([("ansired", "  ⚠  ESCALATION  —  type  y  to allow  or  n  to deny")])
        if self.pending_session_options is not None:
            return FormattedText([("ansicyan", "  Type a session number  ·  Enter = #1  ·  q = cancel")])
        # Normal footer: arc · [session] · [tokens] · keybindings
        sid_chunk = ""
        if self.session_id:
            sid = self.session_id
            # Keep the SES prefix (it identifies what this ID is) and the last
            # 10 chars (the unique tail people actually scan for) — the middle
            # of a ULID is uniform and not useful to look at.
            if len(sid) > 14:
                sid_chunk = f"{sid[:3]}…{sid[-10:]}"
            else:
                sid_chunk = sid

        total = self.total_tokens_in + self.total_tokens_out
        parts: list = [("ansigray", "  arc")]
        if sid_chunk:
            parts.append(("ansigray", "  ·  "))
            parts.append(("ansibrightblack", sid_chunk))
        if total:
            parts.append(("ansigray", "  ·  "))
            parts.append(("ansicyan", f"{total:,} tokens"))
            parts.append(("ansigray", f"  ({self.total_tokens_in:,} in / {self.total_tokens_out:,} out)"))
        parts.append(("ansigray", "  ·  /help  ·  ESC: pause  ·  Ctrl+D: exit"))
        return FormattedText(parts)

    def queue_message(self, text: str) -> None:
        self._pending_messages.append(text)

    def pop_pending(self) -> str | None:
        return self._pending_messages.pop(0) if self._pending_messages else None
