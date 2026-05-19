"""Interactive TUI application.

Design (per phase 0 spec §11 + the conversation that led to it):
  - prompt_toolkit Application in INLINE mode (full_screen=False).
    Inline mode keeps the conversation in the terminal's native scrollback —
    PageUp/mouse-wheel "just work" via the terminal, no custom scroll buffer
    required. Alt-screen mode would have made conversation lost on PageUp.
  - patch_stdout() lets `print()`/Rich output land above the live prompt
    region without clobbering it.
  - The TUI registers itself as an `on_event` plugin so it can render LLM
    responses and tool calls in real time, not just at end of turn.
  - Rich.Console drives all rendering; prompt_toolkit handles input only.

Slash commands (matched first; not sent to the model):
    /help, /exit, /quit, /clear, /sessions
"""
from __future__ import annotations

import sys
from typing import Any, Callable

from rich.console import Console
from rich.status import Status

from arc.config import Config
from arc.runtime.events import EventType, RuntimeEvent
from arc.runtime.loop import AgentSession
from arc.tui import render


# Type alias for the prompt function (injectable for tests)
PromptFn = Callable[[str], str]


class TUIApp:
    """The interactive shell. Wraps an AgentSession.

    Construction is intentionally side-effect-free — call `.run()` to start.
    Tests can inject a custom `prompt_fn` to drive input without a real TTY.
    """

    name = "tui-app"  # used by HookRegistry as plugin name when registered

    def __init__(
        self,
        config: Config,
        session: AgentSession,
        *,
        home_display: str,
        prompt_fn: PromptFn | None = None,
        console: Console | None = None,
    ) -> None:
        self._cfg = config
        self._session = session
        self._home_display = home_display
        self._console = console or Console()
        self._prompt_fn = prompt_fn  # None → use prompt_toolkit at .run() time
        self._status: Status | None = None
        self._last_tokens_in = 0
        self._last_tokens_out = 0
        self._event_count = 0

    # ── Entry point ────────────────────────────────────────────────────────

    def run(self) -> int:
        """Start the interactive loop. Returns process exit code."""
        # Register as on_event plugin so we render in real time.
        # Hook priority high (after recorder) so events are persisted first.
        self._session.registry.register(
            self,
            hooks_order={"on_event": 200},
        )
        self._session.start()

        # SIGINT handler: during agent turns (prompt_toolkit not active),
        # Ctrl+C should pause the agent gracefully, not kill the process.
        # During prompt input, prompt_toolkit intercepts Ctrl+C as a key
        # and raises KeyboardInterrupt before this handler fires.
        prev_sigint = self._install_pause_on_sigint()

        # Print the session banner once
        self._console.print(render.render_session_banner(
            provider=self._cfg.provider.name,
            model=self._cfg.provider.model,
            session_id=self._session.session_id,
            home=self._home_display,
            tools=self._session.tools.names(),
        ))

        prompt = self._resolve_prompt_fn()

        try:
            while True:
                try:
                    text = prompt(self._cfg.tui.prompt_prefix)
                except (EOFError, KeyboardInterrupt):
                    self._console.print()
                    break

                text = text.strip()
                if not text:
                    continue

                if text.startswith("/"):
                    if self._handle_slash(text):
                        break  # /exit or /quit returned True
                    continue

                # Echo the user input into scrollback (above the live prompt area)
                self._console.print(
                    render.render_user_message(text, self._cfg.tui.prompt_prefix)
                )

                # Run the turn synchronously. Real-time updates come from on_event.
                outcome = self._session.run_turn(text)

                # Footer line after each turn
                self._console.print(render.render_footer_line(
                    tokens_in=self._last_tokens_in,
                    tokens_out=self._last_tokens_out,
                    n_events=self._event_count,
                    show_events=self._cfg.tui.show_event_count,
                ))

                if not outcome.success and outcome.error:
                    self._console.print(f"[red]turn error: {outcome.error}[/red]")
        finally:
            self._stop_status()
            self._session.end()
            self._restore_sigint(prev_sigint)

        return 0

    # ── on_event hook (real-time rendering) ────────────────────────────────

    def on_event(self, ctx, event: RuntimeEvent) -> None:
        self._event_count += 1
        t = event.type

        if t == EventType.LLM_CALL_STARTED:
            self._start_status("thinking", style="bold cyan")

        elif t == EventType.LLM_CALL_COMPLETED:
            self._stop_status()
            # Update token counts (may be one of several calls in a turn)
            self._last_tokens_in += event.payload.get("input_tokens", 0)
            self._last_tokens_out += event.payload.get("output_tokens", 0)
            # Render the text portion of the response (tool calls render at TOOL_CALL_*)
            blocks = event.content.get("response_content", [])
            text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            full_text = "".join(text_parts).strip()
            if full_text:
                self._console.print(render.render_assistant_text(full_text))

        elif t == EventType.LLM_CALL_FAILED:
            self._stop_status()
            msg = event.payload.get("exception_message", "")
            self._console.print(f"[red]LLM call failed: {msg}[/red]")

        elif t == EventType.TOOL_CALL_STARTED:
            self._stop_status()
            self._console.print(render.render_tool_call(
                tool_name=event.payload.get("tool_name", "?"),
                tool_input=event.content.get("input", {}),
            ))
            self._start_status(
                f"running {event.payload.get('tool_name', '?')}",
                style="dim yellow",
            )

        elif t == EventType.TOOL_CALL_COMPLETED:
            self._stop_status()
            self._console.print(render.render_tool_result(
                tool_name=event.payload.get("tool_name", "?"),
                output=event.content.get("output", ""),
                ok=event.payload.get("ok", True),
            ))

        elif t == EventType.TOOL_CALL_FAILED:
            self._stop_status()
            self._console.print(render.render_tool_result(
                tool_name=event.payload.get("tool_name", "?"),
                output=event.payload.get("error_message", "(no message)"),
                ok=False,
            ))

        elif t == EventType.TOOL_CALL_DENIED:
            self._stop_status()
            self._console.print(render.render_tool_denied(
                tool_name=event.payload.get("tool_name", "?"),
                reason=event.payload.get("reason", ""),
            ))

        elif t == EventType.TURN_STARTED:
            # Reset per-turn counters
            self._last_tokens_in = 0
            self._last_tokens_out = 0

    # ── Slash commands ─────────────────────────────────────────────────────

    def _handle_slash(self, text: str) -> bool:
        """Return True if the command should end the session."""
        cmd = text.split()[0].lower()
        if cmd in ("/exit", "/quit"):
            return True
        if cmd == "/help":
            self._console.print(render.render_help())
            return False
        if cmd == "/clear":
            self._console.print("[dim]/clear: conversation reset is not yet implemented[/dim]")
            return False
        if cmd == "/sessions":
            self._console.print("[dim]/sessions: try `arc sessions` in another shell[/dim]")
            return False
        self._console.print(f"[red]unknown command: {cmd}  (try /help)[/red]")
        return False

    # ── Status spinner ────────────────────────────────────────────────────

    def _start_status(self, msg: str, *, style: str) -> None:
        self._stop_status()
        self._status = self._console.status(f"[{style}]{msg}...")
        self._status.start()

    def _stop_status(self) -> None:
        if self._status is not None:
            try:
                self._status.stop()
            except Exception:
                pass
            self._status = None

    # ── SIGINT handling ───────────────────────────────────────────────────

    def _install_pause_on_sigint(self):
        """Set a SIGINT handler that pauses the agent (instead of killing it).

        Returns the previous handler so we can restore it on exit.

        Only takes effect when prompt_toolkit isn't actively reading input.
        While reading, prompt_toolkit intercepts Ctrl+C as a key and raises
        KeyboardInterrupt — that path already exits the TUI cleanly.
        """
        import signal
        # Find the pause-resume plugin if registered
        pause_plugin = self._find_pause_plugin()
        if pause_plugin is None:
            return None  # nothing to do — no plugin to receive the trigger

        def _handler(signum, frame):
            try:
                pause_plugin.request_pause()
            except Exception:
                pass  # last thing we want is a signal handler raising

        try:
            return signal.signal(signal.SIGINT, _handler)
        except ValueError:
            # signal() can't be called from non-main thread (e.g., tests)
            return None

    def _restore_sigint(self, prev) -> None:
        import signal
        if prev is None:
            return
        try:
            signal.signal(signal.SIGINT, prev)
        except (ValueError, TypeError):
            pass

    def _find_pause_plugin(self):
        """Locate the pause-resume plugin instance in the registry."""
        for hook_name, chain in self._session.registry._chains.items():
            for _priority, name, method in chain:
                if name == "pause-resume":
                    # The method is bound — get the instance from __self__
                    return getattr(method, "__self__", None)
        return None

    # ── Prompt resolution ─────────────────────────────────────────────────

    def _resolve_prompt_fn(self) -> PromptFn:
        """Return the function we'll call to read user input.

        Tests pass `prompt_fn=` explicitly. Production uses prompt_toolkit
        in inline mode (no alt-screen) with patch_stdout so Rich output
        lands cleanly above the live prompt region.
        """
        if self._prompt_fn is not None:
            return self._prompt_fn

        from prompt_toolkit import PromptSession
        from prompt_toolkit.patch_stdout import patch_stdout

        # PromptSession persists prompt-toolkit state (history, etc.)
        # across calls so up-arrow recall works naturally.
        pt_session = PromptSession()
        patch_ctx = patch_stdout(raw=True)
        patch_ctx.__enter__()
        # Note: we never exit patch_stdout — it's bound to the lifetime of
        # the TUI app. The CLI process exits when the loop ends.

        import sys

        def _prompt(prefix: str) -> str:
            # `erase_when_done` on PromptSession.prompt() isn't portable across
            # prompt_toolkit versions, so we erase manually with ANSI escapes:
            # after the user hits Enter, prompt_toolkit echoes the line and
            # moves to the next line. We move back up and clear the prompt
            # line so render_user_message's purple version takes its place.
            text = pt_session.prompt(prefix)
            # \033[F = move cursor up one line + go to column 0
            # \033[2K = clear entire line
            sys.stdout.write("\033[F\033[2K")
            sys.stdout.flush()
            return text

        return _prompt
