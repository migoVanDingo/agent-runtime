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
        # Session-level running totals (for the bottom toolbar)
        self._session_tokens_in = 0
        self._session_tokens_out = 0
        self._session_turn_count = 0
        # Pricing lookup — built lazily on first toolbar evaluation
        self._pricing = None

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

        # Detect resumed_from for the banner so the user knows this isn't fresh
        resumed_from = self._read_resumed_from_meta()

        # Print the session banner once
        self._console.print(render.render_session_banner(
            provider=self._cfg.provider.name,
            model=self._cfg.provider.model,
            session_id=self._session.session_id,
            home=self._home_display,
            tools=self._session.tools.names(),
            resumed_from=resumed_from,
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

                # Visual separator between turns (skip after the very first one)
                self._console.print(render.render_turn_separator())

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
            in_t = event.payload.get("input_tokens", 0)
            out_t = event.payload.get("output_tokens", 0)
            self._last_tokens_in += in_t
            self._last_tokens_out += out_t
            self._session_tokens_in += in_t
            self._session_tokens_out += out_t
            # Render thinking blocks (gated by config) and text portion
            blocks = event.content.get("response_content", [])
            if self._cfg.tui.show_thinking:
                thinking_parts = [b.get("text", "") for b in blocks
                                  if b.get("type") == "thinking"]
                thinking_text = "".join(thinking_parts).strip()
                if thinking_text:
                    self._console.print(render.render_thinking(thinking_text))
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
                max_lines=self._cfg.tui.tool_output_max_lines,
            ))

        elif t == EventType.TOOL_CALL_FAILED:
            self._stop_status()
            self._console.print(render.render_tool_result(
                tool_name=event.payload.get("tool_name", "?"),
                output=event.payload.get("error_message", "(no message)"),
                ok=False,
                max_lines=self._cfg.tui.tool_output_max_lines,
            ))

        elif t == EventType.TOOL_CALL_DENIED:
            self._stop_status()
            self._console.print(render.render_tool_denied(
                tool_name=event.payload.get("tool_name", "?"),
                reason=event.payload.get("reason", ""),
            ))

        elif t == EventType.TURN_STARTED:
            # Reset per-turn counters; bump session turn counter
            self._last_tokens_in = 0
            self._last_tokens_out = 0
            self._session_turn_count += 1

        elif t == EventType.RUNTIME_CONTEXT_PACKED:
            p = event.payload
            self._console.print(
                f"[dim]context packed: {p.get('n_messages_before', '?')} → "
                f"{p.get('n_messages_after', '?')} messages, "
                f"{p.get('bytes_dropped', 0)} bytes dropped[/dim]"
            )

        elif t == EventType.RUNTIME_CYCLE_DETECTED:
            self._stop_status()
            self._console.print(
                f"[bold yellow]⚠ cycle detected — forcing wrap-up[/bold yellow]"
            )

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
            self._handle_clear()
            return False
        if cmd == "/sessions":
            self._handle_sessions_list()
            return False
        if cmd == "/replay":
            self._handle_replay_menu()
            return False
        self._console.print(f"[red]unknown command: {cmd}  (try /help)[/red]")
        return False

    def _handle_replay_menu(self) -> None:
        """Drop into the 0019 replay menu mid-session.

        Spawns `arc replay` as a subprocess so the menu's prompt_toolkit
        screen doesn't conflict with the live TUI.  Returns the user to
        the running session when done.
        """
        import os
        import subprocess
        import sys
        from arc.bootstrap import resolve_home
        home = resolve_home()
        argv = [sys.executable, "-m", "arc.cli", "--home", str(home), "replay"]
        self._console.print("[dim]launching replay menu (this session continues after)…[/dim]")
        subprocess.run(argv, env=os.environ.copy())

    def _handle_clear(self) -> None:
        """Reset the conversation in place. Same session_id, but the in-memory
        message list is wiped and an event is emitted so the audit trail
        captures the reset.
        """
        n = len(self._session._messages)
        self._session._messages.clear()
        # Emit a conversation.cleared event so events.jsonl + session.log
        # reflect the reset
        try:
            from arc.runtime.events import EventType, RuntimeEvent
            self._session.bus.emit(RuntimeEvent(
                type=EventType.CONVERSATION_CLEARED,
                stage="TUIApp",
                payload={"n_messages_cleared": n},
            ))
        except Exception:
            pass
        # Reset per-session counters that the toolbar tracks
        self._session_tokens_in = 0
        self._session_tokens_out = 0
        self._session_turn_count = 0
        self._console.print(
            f"[dim]conversation cleared ({n} messages removed; session continues)[/dim]"
        )

    def _handle_sessions_list(self) -> None:
        """Render the sessions index as a Rich table inline."""
        # Find ARC_HOME via the recorder plugin's session_dir parent (the
        # cleanest way to discover it without re-resolving env vars).
        sessions_dir = self._find_sessions_dir()
        if sessions_dir is None:
            self._console.print("[dim]/sessions: could not locate sessions directory[/dim]")
            return
        index_path = sessions_dir / "index.jsonl"
        if not index_path.exists():
            self._console.print("[dim]no sessions recorded yet[/dim]")
            return
        self._console.print(render.render_sessions_table(sessions_dir, index_path))

    def _find_sessions_dir(self):
        """Walk registered plugins to find the recorder's session_dir parent."""
        for hook_name, chain in self._session.registry._chains.items():
            for _priority, name, method in chain:
                if name == "jsonl-recorder":
                    plugin = getattr(method, "__self__", None)
                    if plugin is not None:
                        # JSONLRecorder._session_dir.parent is sessions/
                        return getattr(plugin, "_session_dir", None) and \
                               plugin._session_dir.parent
        return None

    def _read_resumed_from_meta(self) -> str | None:
        """If the current session's meta.json has `resumed_from`, return it.

        Used by the banner to flag resumed sessions. Returns None for fresh
        sessions or if meta isn't readable yet.
        """
        sessions_dir = self._find_sessions_dir()
        if sessions_dir is None:
            return None
        meta_path = sessions_dir / self._session.session_id / "meta.json"
        if not meta_path.exists():
            return None
        try:
            import json
            meta = json.loads(meta_path.read_text())
            return meta.get("resumed_from")
        except Exception:
            return None

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

        Wires up:
        - ↑/↓ recall via FileHistory in ARC_HOME (when input_history_enabled)
        - Tab-completion for slash commands
        - bottom_toolbar callable for the persistent provider/tokens/$ line
          (when toolbar_enabled)
        """
        if self._prompt_fn is not None:
            return self._prompt_fn

        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory, InMemoryHistory
        from prompt_toolkit.patch_stdout import patch_stdout
        from prompt_toolkit.styles import Style

        # Tab-completion for slash commands
        slash_completer = WordCompleter(
            ["/help", "/exit", "/quit", "/clear", "/sessions", "/replay"],
            ignore_case=True,
        )

        # History: persisted to ARC_HOME/history when enabled, in-memory otherwise
        if self._cfg.tui.input_history_enabled:
            history_path = self._resolve_history_path()
            if history_path is not None:
                history = FileHistory(str(history_path))
            else:
                history = InMemoryHistory()
        else:
            history = InMemoryHistory()

        # Bottom toolbar callable — evaluated each prompt() call
        bottom_toolbar = self._build_bottom_toolbar_fn() if self._cfg.tui.toolbar_enabled else None

        # Custom style: prompt_toolkit's default `bottom-toolbar` is reverse
        # video (white-on-bright), which we found visually loud. Override to
        # a soft grey on default bg so it sits quietly below the prompt.
        toolbar_style = Style.from_dict({
            "bottom-toolbar":          "noreverse fg:#7a7a7a bg:default",
            "toolbar.provider":        "fg:#8aa0c0 bg:default",
            "toolbar.sid":             "fg:#7a7a7a bg:default",
            "toolbar.turn":            "fg:#7a7a7a bg:default",
            "toolbar.tokens":          "fg:#8a8a6a bg:default",
            "toolbar.cost":            "fg:#7a9a7a bg:default",
            "toolbar.sep":             "fg:#4a4a4a bg:default",
        })

        pt_session = PromptSession(
            history=history,
            completer=slash_completer,
            complete_while_typing=False,  # only on Tab; avoids interrupting input
            bottom_toolbar=bottom_toolbar,
            style=toolbar_style,
        )
        patch_ctx = patch_stdout(raw=True)
        patch_ctx.__enter__()
        # Note: we never exit patch_stdout — it's bound to the lifetime of
        # the TUI app. The CLI process exits when the loop ends.

        import sys

        def _prompt(prefix: str) -> str:
            # Blank line above the prompt so the input doesn't sit flush
            # against the previous turn's footer / toolbar.
            sys.stdout.write("\n")
            sys.stdout.flush()
            # erase_when_done removes the entire prompt render area on
            # submit (handles wrapped multi-line input correctly). Falls
            # back to a single-line ANSI erase on older prompt_toolkit.
            try:
                text = pt_session.prompt(prefix, erase_when_done=True)
            except TypeError:
                text = pt_session.prompt(prefix)
                sys.stdout.write("\033[F\033[2K")
                sys.stdout.flush()
            return text

        return _prompt

    def _resolve_history_path(self):
        """Locate ARC_HOME/history (parent of sessions_dir)."""
        sessions_dir = self._find_sessions_dir()
        if sessions_dir is None:
            return None
        return sessions_dir.parent / "history"

    # ── Bottom toolbar ────────────────────────────────────────────────────

    def _build_bottom_toolbar_fn(self):
        """Returns a callable prompt_toolkit invokes each prompt() to render
        the bottom toolbar. Re-evaluated per prompt so it reflects the
        latest stats after each turn.
        """
        from prompt_toolkit.formatted_text import FormattedText

        def _toolbar():
            return FormattedText(list(self._toolbar_segments()))

        return _toolbar

    def _toolbar_segments(self):
        """Yield (style, text) tuples for the toolbar line.

        Layout: provider/model · SES01...XYZ12 · turn N · in→out (total) · $cost
        Cost column is dropped entirely if pricing isn't available.
        """
        provider = self._cfg.provider.name
        model = self._cfg.provider.model
        sid = self._session.session_id
        # Show head + tail so the user can disambiguate sessions whose ids
        # share a prefix (e.g. "20260520..." sequences from the same day)
        if len(sid) > 14:
            sid_short = f"{sid[:6]}...{sid[-5:]}"
        else:
            sid_short = sid

        # provider/model
        yield ("class:toolbar.provider", f" {provider}/{model} ")
        yield ("class:toolbar.sep", "· ")

        # session id
        yield ("class:toolbar.sid", f"{sid_short} ")
        yield ("class:toolbar.sep", "· ")

        # turn count
        yield ("class:toolbar.turn", f"turn {self._session_turn_count} ")
        yield ("class:toolbar.sep", "· ")

        # tokens (last turn / cumulative session)
        in_t = self._last_tokens_in
        out_t = self._last_tokens_out
        total = self._session_tokens_in + self._session_tokens_out
        yield ("class:toolbar.tokens", f"{in_t}→{out_t} ({total:,} total) ")

        # cost (only if pricing is available)
        cost = self._compute_session_cost()
        if cost is not None:
            yield ("class:toolbar.sep", "· ")
            from arc.tui.pricing import format_cost
            yield ("class:toolbar.cost", f" {format_cost(cost)} ")

    def _compute_session_cost(self) -> float | None:
        """Look up pricing on first call; reuse on subsequent calls."""
        if self._pricing is None:
            from arc.tui.pricing import PricingTable
            sessions_dir = self._find_sessions_dir()
            if sessions_dir is None:
                return None
            cache_path = sessions_dir.parent / "pricing_cache.json"
            self._pricing = PricingTable(cache_path=cache_path)
        return self._pricing.estimate_cost_usd(
            provider=self._cfg.provider.name,
            model=self._cfg.provider.model,
            input_tokens=self._session_tokens_in,
            output_tokens=self._session_tokens_out,
        )
