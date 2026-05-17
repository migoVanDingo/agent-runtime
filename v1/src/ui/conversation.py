"""ConversationModel — stores conversation as formatted text, manages scrolling.

Layout contract:
  - get_line_prefix on the conv Window adds 2 spaces to EVERY visual line.
  - Content in this model therefore has NO leading spaces — the prefix handles
    the left margin uniformly for everything.
  - Visual result: ▶ at col 2, Agent at col 2, all text at col 2 minimum.
"""
from __future__ import annotations

import re
from prompt_toolkit.formatted_text import to_formatted_text, ANSI

def _render_markdown_to_ansi(text: str) -> str:
    """Render Markdown to ANSI via Rich, stripping OSC sequences first.

    Two known issues fixed here:

    1. OSC truncation: Rich outputs OSC 8 hyperlink escape sequences for URLs.
       prompt_toolkit's ANSI parser stops at the first unrecognised escape and
       silently drops everything after it. Stripping OSC sequences first fixes this.

    2. Width truncation: Rich clips text at the Console `width`. Using a fixed
       width smaller than the terminal (e.g. 100) cuts sentences in list items.
       We now use the actual terminal width so Rich wraps but never clips.
    """
    import io
    import shutil
    from rich.console import Console
    from rich.markdown import Markdown

    # Use terminal width so Rich never clips content. The Window handles visual
    # wrapping inside the application — Rich just needs to output full lines.
    term_width = shutil.get_terminal_size((120, 40)).columns

    buf = io.StringIO()
    con = Console(
        file=buf,
        force_terminal=True,
        highlight=False,
        soft_wrap=False,
        width=term_width,
    )
    con.print(Markdown(text))
    raw = buf.getvalue()

    # Strip OSC escape sequences: \x1b] ... \x1b\ or \x1b] ... \x07
    return re.sub(r"\x1b\][^\x1b\x07]*(?:\x1b\\|\x07)", "", raw)


class ConversationModel:
    def __init__(self):
        self._chunks: list[tuple[str, str]] = []
        self._cursor_idx: int = 0
        self._auto_scroll: bool = True
        self._stream_text: str = ""
        self._streaming: bool = False

    def add(self, style: str, text: str) -> None:
        self._chunks.append((style, text))
        if self._auto_scroll:
            self._cursor_idx = len(self._chunks)

    def add_ansi(self, ansi_text: str) -> None:
        tuples = list(to_formatted_text(ANSI(ansi_text)))
        self._chunks.extend(tuples)
        if self._auto_scroll:
            self._cursor_idx = len(self._chunks)

    # ── Message types ─────────────────────────────────────────────────────────
    # NOTE: No leading spaces in any content — the Window's get_line_prefix adds
    # 2 spaces to every visual line, providing a consistent left margin.

    def add_user_message(self, text: str) -> None:
        # ▶ lands at col 2 (from 2-space prefix), text at col 5 (after ▶  )
        self.add("ansigreen bold", "\n▶  ")
        self.add("", text + "\n")

    def begin_agent_response(self) -> None:
        # "Agent" lands at col 2 (same as ▶)
        self.add("ansicyan bold", "\nAgent\n")
        self._stream_text = ""
        self._streaming = True

    def append_token(self, text: str) -> None:
        self._stream_text += text
        if self._auto_scroll:
            self._cursor_idx = len(self._chunks) + 1

    def finalize_agent_response(self, full_text: str) -> None:
        self._streaming = False
        self._stream_text = ""
        if full_text.strip():
            self.add_ansi(_render_markdown_to_ansi(full_text))
        else:
            self.add("", "\n")

    def add_timer(self, elapsed_ms: int, tokens_in: int = 0, tokens_out: int = 0) -> None:
        m, s = divmod(elapsed_ms // 1000, 60)
        parts = [f"⏱  {m}:{s:02d}"]
        if tokens_in or tokens_out:
            total = tokens_in + tokens_out
            parts.append(f"{total:,} tokens  ({tokens_in:,} in / {tokens_out:,} out)")
        self.add("ansigray", "   ·   ".join(parts) + "\n\n")

    def add_error(self, error: str) -> None:
        self.add("ansired bold", "\nError: ")
        self.add("ansired", error + "\n\n")

    def add_cancelled(self) -> None:
        self.add("ansiyellow", "\nTurn cancelled.\n\n")

    def add_queued(self) -> None:
        self.add("ansigray", "(queued — will send after current turn)\n")

    def add_welcome(self, session_id: str, session_dir: str, provider_line: str) -> None:
        """Render the startup landing screen at the top of the conversation.

        Includes the ARC logo, session info, and a quick command reference.
        Called once at session start by ui.app._interactive.
        """
        # ── ARC logo (slant figlet style) ─────────────────────────────────
        self.add("ansicyan bold", "\n")
        self.add("ansicyan bold", "       ___    ____  ______\n")
        self.add("ansicyan bold", "      /   |  / __ \\/ ____/\n")
        self.add("ansicyan bold", "     / /| | / /_/ / /     \n")
        self.add("ansicyan bold", "    / ___ |/ _, _/ /___   \n")
        self.add("ansicyan bold", "   /_/  |_/_/ |_|\\____/   \n")
        self.add("ansigray",      "           agent runtime\n\n")

        # ── Session info ──────────────────────────────────────────────────
        self.add("ansigray", "Session    ")
        self.add("", session_id + "\n")
        self.add("ansigray", "Dir        ")
        self.add("", session_dir + "\n")
        self.add("ansigray", "Model      ")
        self.add("", provider_line + "\n\n")

        # ── Quick command reference ───────────────────────────────────────
        self.add("ansigray bold", "── Quick start ────────────────────────────────────────\n")
        commands = [
            ("/help",     "list all commands"),
            ("/pause",    "pause the running agent  (or  ESC)"),
            ("/resume",   "resume a paused agent"),
            ("/cancel",   "cancel the current turn"),
            ("/clear",    "clear the conversation"),
            ("/settings", "show settings"),
            ("/exit",     "exit arc-tui"),
        ]
        for cmd, desc in commands:
            self.add("ansicyan", f"  {cmd:<11}")
            self.add("ansigray", f"{desc}\n")

        self.add("", "\n")
        self.add("ansigray",
                 "Enter to send  ·  Shift+Enter for newline  ·  PgUp/PgDn to scroll\n\n")

    def add_escalation(self, esc) -> None:
        self.add("ansired bold", f"\n⚠  ESCALATION — {esc.source}\n")
        self.add("", f"{esc.reason}\n")
        if esc.tool_name:
            self.add("ansigray", "Tool:  ")
            self.add("bold", f"{esc.tool_name}\n")
        if esc.tool_input:
            for k, v in list(esc.tool_input.items())[:4]:
                v_str = str(v)
                if len(v_str) > 80:
                    v_str = v_str[:77] + "…"
                self.add("ansigray", f"{k}:  ")
                self.add("", v_str + "\n")
        self.add("", "\n")

    # ── Scrolling ─────────────────────────────────────────────────────────────

    def scroll_up(self, lines: int = 10) -> None:
        self._cursor_idx = max(0, self._cursor_idx - lines * 3)
        self._auto_scroll = False

    def scroll_down(self, lines: int = 10) -> None:
        new_idx = self._cursor_idx + lines * 3
        if new_idx >= len(self._chunks):
            self._auto_scroll = True
            self._cursor_idx = len(self._chunks)
        else:
            self._cursor_idx = new_idx

    # ── Rendering ─────────────────────────────────────────────────────────────

    def get_formatted_text(self) -> list:
        result: list[tuple[str, str]] = []
        cursor_placed = False
        for i, chunk in enumerate(self._chunks):
            if not self._auto_scroll and i == self._cursor_idx:
                result.append(("[SetCursorPosition]", ""))
                cursor_placed = True
            result.append(chunk)
        if self._streaming and self._stream_text:
            result.append(("", self._stream_text))
        if not cursor_placed:
            result.append(("[SetCursorPosition]", ""))
        return result
