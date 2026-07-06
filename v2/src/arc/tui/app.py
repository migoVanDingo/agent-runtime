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
    /help, /exit, /quit, /clear, /sessions, /replay, /rewind, /retry,
    /model, /tab
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.status import Status

from arc.config import Config
from arc.runtime.events import EventType, RuntimeEvent
from arc.runtime.loop import AgentSession
from arc.tui import render
from arc.tui.themes import active as _active_theme
from arc.tui.themes import resolve_from_config

# Type alias for the prompt function (injectable for tests)
PromptFn = Callable[[str], str]


class _Tab:
    """One live session owned by the TUI (0026 phase d).

    A background tab is a live-but-idle session parked between turns —
    the TUI is single-threaded, so only the focused tab ever runs a turn
    and only it emits events. Token/turn counters are per-tab so the
    toolbar restarts with each branch.
    """

    def __init__(self, session: AgentSession) -> None:
        self.session = session
        self.pending_meta: dict | None = None
        self.last_tokens_in = 0
        self.last_tokens_out = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.turn_count = 0


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
        paths: Any = None,  # bootstrap.HomePaths | None — enables /rewind + /retry
        turn_walker: Callable[[list], int | None] | None = None,  # test seam for rewind mode
    ) -> None:
        self._cfg = config
        self._home_display = home_display
        self._paths = paths
        self._turn_walker = turn_walker
        # Time travel (0026): tabs (each a live session; branch opens one)
        # + the armed rewind target (turn number). Session-scoped state —
        # counters, pending meta stamps — lives on the tab.
        self._tabs: list[_Tab] = [_Tab(session)]
        self._focus = 0
        self._rewind_target: int | None = None
        # Resolve & cache the active theme so render.py + dialogs see it.
        # If the caller injected its own Console (tests), push our theme
        # onto it so the arc.* named styles resolve there too.
        theme = resolve_from_config(config.tui.theme)
        if console is None:
            self._console = Console(theme=theme.rich_theme)
        else:
            self._console = console
            try:
                self._console.push_theme(theme.rich_theme)
            except Exception:
                pass
        self._prompt_fn = prompt_fn  # None → use prompt_toolkit at .run() time
        self._status: Status | None = None
        self._event_count = 0
        # Pricing lookup — built lazily on first toolbar evaluation
        self._pricing = None

    # ── Focused-tab state (kept as properties so the rest of the app reads
    #    like the single-session TUI it used to be) ─────────────────────────

    @property
    def _session(self) -> AgentSession:
        return self._tabs[self._focus].session

    @property
    def _pending_meta(self) -> dict | None:
        return self._tabs[self._focus].pending_meta

    @_pending_meta.setter
    def _pending_meta(self, v: dict | None) -> None:
        self._tabs[self._focus].pending_meta = v

    @property
    def _last_tokens_in(self) -> int:
        return self._tabs[self._focus].last_tokens_in

    @_last_tokens_in.setter
    def _last_tokens_in(self, v: int) -> None:
        self._tabs[self._focus].last_tokens_in = v

    @property
    def _last_tokens_out(self) -> int:
        return self._tabs[self._focus].last_tokens_out

    @_last_tokens_out.setter
    def _last_tokens_out(self, v: int) -> None:
        self._tabs[self._focus].last_tokens_out = v

    @property
    def _session_tokens_in(self) -> int:
        return self._tabs[self._focus].tokens_in

    @_session_tokens_in.setter
    def _session_tokens_in(self, v: int) -> None:
        self._tabs[self._focus].tokens_in = v

    @property
    def _session_tokens_out(self) -> int:
        return self._tabs[self._focus].tokens_out

    @_session_tokens_out.setter
    def _session_tokens_out(self, v: int) -> None:
        self._tabs[self._focus].tokens_out = v

    @property
    def _session_turn_count(self) -> int:
        return self._tabs[self._focus].turn_count

    @_session_turn_count.setter
    def _session_turn_count(self, v: int) -> None:
        self._tabs[self._focus].turn_count = v

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
                    text = prompt(self._prompt_prefix())
                except (EOFError, KeyboardInterrupt):
                    self._console.print()
                    if len(self._tabs) > 1:
                        self._close_focused_tab()  # Ctrl+D = close tab, like /exit
                        continue
                    break

                text = text.strip()
                if not text:
                    if self._rewind_target is not None:
                        self._rewind_target = None
                        self._console.print(
                            "[arc.dim]rewind cancelled — back at the tip[/arc.dim]"
                        )
                    continue

                if text.startswith("/"):
                    if self._handle_slash(text):
                        break  # /exit or /quit returned True
                    continue

                # Armed rewind: branch-on-submit — the session is only
                # created now that the user committed a prompt (0026).
                if self._rewind_target is not None:
                    n = self._rewind_target
                    self._rewind_target = None
                    if not self._rebuild_session(n):
                        continue

                self._run_one_turn(text)
        finally:
            self._stop_status()
            for tab in list(self._tabs):  # /quit or fatal exit: end every tab
                self._end_session_and_stamp(tab)
            self._tabs = [self._tabs[0]]  # keep list non-empty for property access
            self._focus = 0
            self._restore_sigint(prev_sigint)

        return 0

    def _run_one_turn(self, text: str) -> None:
        """Echo the prompt, run the turn, print footer + separator."""
        self._console.print(
            render.render_user_message(text, self._cfg.tui.prompt_prefix)
        )

        # Run the turn synchronously. Real-time updates come from on_event.
        outcome = self._session.run_turn(text)

        self._console.print(render.render_footer_line(
            tokens_in=self._last_tokens_in,
            tokens_out=self._last_tokens_out,
            n_events=self._event_count,
            show_events=self._cfg.tui.show_event_count,
        ))
        self._console.print(render.render_turn_separator())

        if not outcome.success and outcome.error:
            self._console.print(f"[arc.error]turn error: {outcome.error}[/arc.error]")

    def _prompt_prefix(self) -> str:
        """Normal prefix, or the armed-rewind variant (⑂N …)."""
        if self._rewind_target is not None:
            return f"⑂{self._rewind_target} {self._cfg.tui.prompt_prefix}"
        return self._cfg.tui.prompt_prefix

    # ── on_event hook (real-time rendering) ────────────────────────────────

    def on_event(self, ctx, event: RuntimeEvent) -> None:
        self._event_count += 1
        t = event.type

        if t == EventType.LLM_CALL_STARTED:
            self._start_status("thinking", style="arc.brand")

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
            self._console.print(f"[arc.error]LLM call failed: {msg}[/arc.error]")

        elif t == EventType.TOOL_CALL_STARTED:
            self._stop_status()
            self._console.print(render.render_tool_call(
                tool_name=event.payload.get("tool_name", "?"),
                tool_input=event.content.get("input", {}),
            ))
            self._start_status(
                f"running {event.payload.get('tool_name', '?')}",
                style="arc.tool.call",
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
                f"[arc.dim]context packed: {p.get('n_messages_before', '?')} → "
                f"{p.get('n_messages_after', '?')} messages, "
                f"{p.get('bytes_dropped', 0)} bytes dropped[/arc.dim]"
            )

        elif t == EventType.RUNTIME_CYCLE_DETECTED:
            self._stop_status()
            self._console.print(
                "[arc.warning]⚠ cycle detected — forcing wrap-up[/arc.warning]"
            )

        elif t == EventType.SUBAGENT_DISPATCHED:
            # Sub-agent dispatch started. Print a header line and start a
            # spinner; progress events will keep updating its message until
            # SUBAGENT_RETURNED/ABORTED stops it.
            p = event.payload
            self._stop_status()
            self._console.print(render.render_subagent_dispatched(
                spec_name=p.get("spec_name", "?"),
                provider=p.get("provider", "?"),
                model=p.get("model", "?"),
                child_session_id=p.get("child_session_id", ""),
            ))
            import time as _t
            self._sub_dispatch_start = _t.monotonic()
            self._sub_dispatch_spec = p.get("spec_name", "?")
            self._start_status(
                f"subagent {self._sub_dispatch_spec} dispatching",
                style="arc.subagent.name",
            )

        elif t == EventType.SUBAGENT_PROGRESS:
            import time as _t
            p = event.payload
            spec = p.get("spec_name", "?")
            msg = p.get("message", "...")
            cet = p.get("child_event_type", "")
            # Stream the child's tool activity into the scrollback (opt-out via
            # tui.subagent_activity) so the user sees what a sub-agent is doing,
            # not just a spinner. Tool starts + failures make a clean trace;
            # LLM/turn events stay ephemeral in the spinner only.
            if self._cfg.tui.subagent_activity and cet in (
                EventType.TOOL_CALL_STARTED, EventType.TOOL_CALL_FAILED,
                EventType.LLM_CALL_FAILED,
            ):
                failed = cet in (EventType.TOOL_CALL_FAILED, EventType.LLM_CALL_FAILED)
                self._stop_status()
                self._console.print(render.render_subagent_activity(
                    message=msg,
                    tool_name=p.get("tool_name"),
                    tool_input=p.get("tool_input"),
                    failed=failed,
                ))
            elapsed = _t.monotonic() - getattr(self, "_sub_dispatch_start", _t.monotonic())
            if self._status is None:
                self._start_status(
                    f"subagent {spec}: {msg} ({elapsed:.0f}s)", style="arc.subagent.name")
            else:
                self._status.update(
                    f"[arc.subagent.name]subagent {spec}: {msg} ({elapsed:.0f}s)[/arc.subagent.name]"
                )

        elif t == EventType.SUBAGENT_RETURNED:
            self._stop_status()
            p = event.payload
            self._console.print(render.render_subagent_done(
                spec_name=p.get("spec_name", "?"),
                status=p.get("status", "ok"),
                turns=int(p.get("turns", 0)),
                tool_calls=int(p.get("tool_calls", 0)),
                cost_usd=float(p.get("cost_usd", 0.0)),
                wallclock_s=float(p.get("wallclock_s", 0.0)),
            ))

        elif t == EventType.SUBAGENT_ABORTED:
            self._stop_status()
            p = event.payload
            self._console.print(render.render_subagent_done(
                spec_name=p.get("spec_name", "?"),
                status=p.get("reason", "aborted"),
                turns=int(p.get("turns", 0)),
                tool_calls=0,
                cost_usd=0.0,
                wallclock_s=float(p.get("wallclock_s", 0.0)),
                error_message=event.content.get("error_message") if event.content else None,
            ))

        elif t == EventType.SUBAGENT_QUOTA_EXCEEDED:
            self._console.print(
                f"[arc.warning]⊘ subagent {event.payload.get('spec_name', '?')} "
                f"quota exceeded ({event.payload.get('cap', '?')}/session)[/arc.warning]"
            )

        elif t == EventType.SUBAGENT_CIRCUIT_TRIPPED:
            self._console.print(
                f"[arc.warning]⚠ subagent {event.payload.get('spec_name', '?')} "
                f"circuit tripped (locked for this session)[/arc.warning]"
            )

        elif t == EventType.SUBAGENT_RETRY_ATTEMPTED:
            p = event.payload
            self._console.print(
                f"[arc.dim]subagent {p.get('spec_name', '?')} transient retry "
                f"#{p.get('attempt', '?')} ({p.get('error_class', '?')})[/arc.dim]"
            )

    # ── Slash commands ─────────────────────────────────────────────────────

    def _handle_slash(self, text: str) -> bool:
        """Return True if the command should end the session."""
        cmd = text.split()[0].lower()
        if cmd == "/quit":
            return True  # closes every tab (run()'s finally ends them all)
        if cmd == "/exit":
            if len(self._tabs) > 1:
                self._close_focused_tab()
                return False
            return True
        if cmd == "/tab":
            self._handle_tab(text)
            return False
        if cmd == "/help":
            self._console.print(render.render_help())
            return False
        if cmd == "/clear":
            self._rewind_target = None  # a cleared conversation has no turns to branch at
            self._handle_clear()
            return False
        if cmd == "/sessions":
            self._handle_sessions_list()
            return False
        if cmd == "/replay":
            self._handle_replay_menu()
            return False
        if cmd == "/rewind":
            self._handle_rewind(text)
            return False
        if cmd == "/retry":
            self._handle_retry()
            return False
        if cmd == "/model":
            self._handle_model(text)
            return False
        self._console.print(f"[arc.error]unknown command: {cmd}  (try /help)[/arc.error]")
        return False

    # ── Time travel (0026) ────────────────────────────────────────────────

    def _handle_rewind(self, text: str) -> None:
        """`/rewind` — print the turn map. `/rewind N` — arm a branch at N.

        Arming is UI-only: nothing is created until the user submits a
        prompt (branch-on-submit). Empty input at the armed prompt cancels.
        """
        if self._paths is None:
            self._console.print(
                "[arc.dim]/rewind unavailable — session has no home paths "
                "(headless/test wiring)[/arc.dim]"
            )
            return
        from arc.resume import count_completed_turns

        source_dir = self._paths.sessions_dir / self._session.session_id
        total = count_completed_turns(source_dir)
        if total == 0:
            self._console.print("[arc.dim]no completed turns to rewind to yet[/arc.dim]")
            return

        parts = text.split()
        if len(parts) == 1:
            from arc.replay.compare import extract_turns
            turns = extract_turns(source_dir)
            self._console.print(render.render_turn_map(turns))
            walker_available = self._turn_walker is not None or self._prompt_fn is None
            n = self._run_turn_walker(turns)
            if n is not None:
                self._arm_rewind(n, total)
            elif walker_available:
                self._console.print("[arc.dim]rewind cancelled — still at the tip[/arc.dim]")
            return

        try:
            n = int(parts[1])
        except ValueError:
            self._console.print(f"[arc.error]/rewind: not a turn number: {parts[1]}[/arc.error]")
            return
        if n < 0:
            self._console.print("[arc.error]/rewind: turn must be >= 0[/arc.error]")
            return
        if n > total:
            self._console.print(
                f"[arc.dim]/rewind: only {total} completed turns; clamping to {total}[/arc.dim]"
            )
            n = total

        self._arm_rewind(n, total)

    def _run_turn_walker(self, turns: list) -> int | None:
        """Run the ←/→ rewind walker; returns the selected turn or None.

        Test seam first; the real prompt_toolkit walker only when the main
        prompt is real prompt_toolkit too (injected prompt_fn = no TTY —
        the printed map + `/rewind N` remain the non-interactive path).
        """
        if not turns:
            return None
        if self._turn_walker is not None:
            return self._turn_walker(turns)
        if self._prompt_fn is not None:
            return None
        from arc.tui.rewind_mode import walk_turns
        total = len(turns)
        return walk_turns(
            turns,
            print_card=lambda i: self._console.print(
                render.render_turn_card(turns[i - 1], total)
            ),
        )

    def _arm_rewind(self, n: int, total: int) -> None:
        self._rewind_target = n
        self._console.print(
            f"[arc.brand]⑂[/arc.brand] rewound to turn {n}/{total} — "
            f"next prompt branches there  [arc.dim](empty input cancels)[/arc.dim]"
        )

    def _handle_retry(self) -> None:
        """`/retry` — rewind one turn and re-ask the same prompt verbatim."""
        if self._paths is None:
            self._console.print(
                "[arc.dim]/retry unavailable — session has no home paths "
                "(headless/test wiring)[/arc.dim]"
            )
            return
        from arc.resume import count_completed_turns

        source_dir = self._paths.sessions_dir / self._session.session_id
        total = count_completed_turns(source_dir)
        if total == 0:
            self._console.print("[arc.dim]no completed turn to retry[/arc.dim]")
            return

        last_input = self._recorded_user_input(source_dir, total)
        if last_input is None:
            self._console.print("[arc.error]/retry: could not recover the last prompt[/arc.error]")
            return

        self._rewind_target = None
        if self._rebuild_session(total - 1, retry_of_turn=total):
            self._run_one_turn(last_input)

    def _handle_model(self, text: str) -> None:
        """`/model` — show current provider/model. `/model X` — continue this
        conversation on model X (same provider) or `/model prov/X` to switch
        providers. Session-scoped: config.yml is not touched.
        """
        from dataclasses import replace

        cur = self._cfg.provider
        parts = text.split()
        if len(parts) == 1:
            from arc.setup.picker import _PROVIDER_DEFAULTS
            self._console.print(
                f"current: [arc.info]{cur.name}/{cur.model}[/arc.info]\n"
                f"[arc.dim]usage: /model <model>  (same provider)   "
                f"/model <provider>/<model>\n"
                f"providers: {', '.join(sorted(_PROVIDER_DEFAULTS))}[/arc.dim]"
            )
            return
        if self._paths is None:
            self._console.print(
                "[arc.dim]/model unavailable — session has no home paths "
                "(headless/test wiring)[/arc.dim]"
            )
            return

        arg = parts[1]
        if "/" in arg:
            pname, _, model = arg.partition("/")
        else:
            pname, model = cur.name, arg
        if not model:
            self._console.print("[arc.error]/model: missing model name[/arc.error]")
            return

        if pname == cur.name:
            new_pcfg = replace(cur, model=model)
        else:
            from arc.setup.picker import _PROVIDER_DEFAULTS
            d = _PROVIDER_DEFAULTS.get(pname)
            if d is None:
                self._console.print(
                    f"[arc.error]/model: unknown provider {pname!r} "
                    f"(known: {', '.join(sorted(_PROVIDER_DEFAULTS))})[/arc.error]"
                )
                return
            new_pcfg = replace(
                cur, name=pname, model=model,
                api_key_env=d["api_key_env"], base_url=d["base_url"],
            )

        if new_pcfg.name == cur.name and new_pcfg.model == cur.model:
            self._console.print(f"[arc.dim]already on {cur.name}/{cur.model}[/arc.dim]")
            return

        # Construct the new provider BEFORE touching the running session so a
        # bad name / missing API key aborts cleanly with the session intact.
        try:
            from arc.providers import build as build_provider
            new_provider = build_provider(new_pcfg)
        except Exception as exc:
            self._console.print(
                f"[arc.error]/model: could not construct {new_pcfg.name}/"
                f"{new_pcfg.model}: {exc} — session unchanged[/arc.error]"
            )
            return

        from arc.resume import count_completed_turns
        total = count_completed_turns(
            self._paths.sessions_dir / self._session.session_id)
        self._rewind_target = None
        self._rebuild_session(total, provider=new_provider, provider_cfg=new_pcfg)

    def _rebuild_session(self, max_turns: int, *, retry_of_turn: int | None = None,
                         provider=None, provider_cfg=None) -> bool:
        """The 0026 core primitive: end the current session, start a new one
        seeded with the first `max_turns` turns of its recording, stamp
        lineage. The TUI process (and this app instance) keep running.
        """
        if self._paths is None:
            self._console.print("[arc.error]branch unavailable — no home paths[/arc.error]")
            return False
        from arc.cli.wiring import build_session
        from arc.resume import count_completed_turns, messages_from_session
        from arc.tools import build as build_tools
        from arc.user_gate import TUIGate

        if len(self._tabs) >= self._cfg.tui.tabs_max:
            self._console.print(
                f"[arc.error]tab cap reached ({len(self._tabs)}/"
                f"{self._cfg.tui.tabs_max}) — /exit a tab first "
                f"(tui.tabs_max in config)[/arc.error]"
            )
            return False

        old_sid = self._session.session_id
        source_dir = self._paths.sessions_dir / old_sid

        # The parent stays live in its tab. Its recording is still safe to
        # truncate from: the recorder appends per event, and a tab between
        # turns is complete through its last turn.ended — the same
        # between-turns invariant pause relies on.
        n = max(0, min(max_turns, count_completed_turns(source_dir)))
        try:
            own_messages = messages_from_session(source_dir, max_turns=n)
        except FileNotFoundError:
            self._console.print(
                "[arc.error]branch failed: no recording found (recorder disabled?) — "
                "session ended; restart arc[/arc.error]"
            )
            return False
        # A branched/resumed session's recording contains only its OWN turns —
        # the inherited prefix lives in initial_messages and never becomes
        # events. Prepend it or a second-generation branch silently loses
        # everything before the previous branch point. Turn numbers stay
        # session-local: /rewind 0 in a branch = back to its branch point.
        messages = list(self._session.initial_messages or []) + own_messages

        # /model swap: effective config diverges from config.yml, so the new
        # session's snapshot must carry the override — replay reconstructs
        # from the snapshot (see render_provider_override).
        old_pcfg = self._cfg.provider
        snapshot_override = None
        if provider_cfg is not None:
            from dataclasses import replace

            from arc.setup.writer import render_provider_override
            self._cfg = replace(self._cfg, provider=provider_cfg)
            try:
                snapshot_override = render_provider_override(
                    self._paths.config_file.read_text(),
                    name=provider_cfg.name, model=provider_cfg.model,
                    base_url=provider_cfg.base_url,
                    api_key_env=provider_cfg.api_key_env,
                )
            except ValueError:
                self._console.print(
                    "[arc.dim]warning: config has no provider block; snapshot "
                    "keeps the file config — replay of this branch will use "
                    "the wrong model[/arc.dim]"
                )

        # Fresh tools + plugins per session (plugin lifecycle contract:
        # provides_tools re-merges on start). The provider instance carries
        # no session state — reuse it unless /model swapped it. Sub-agent
        # specs are static config — rediscovery would be redundant.
        built = build_session(
            self._cfg, self._paths,
            provider=provider if provider is not None else self._session.provider,
            tools=build_tools(self._cfg.tools),
            subagent_registry=self._session.subagent_registry,
            gate=TUIGate(console=self._console),
            initial_messages=messages,
            config_snapshot_yaml=snapshot_override,
        )
        self._tabs.append(_Tab(built.session))
        self._focus = len(self._tabs) - 1
        self._session.registry.register(self, hooks_order={"on_event": 200})
        self._session.start()

        payload = {
            "source_session_id": old_sid,
            "branched_at_turn": n,
            "restored_message_count": len(messages),
        }
        if retry_of_turn is not None:
            payload["retry_of_turn"] = retry_of_turn
        self._session.bus.emit(RuntimeEvent(
            type=EventType.SESSION_BRANCHED,
            stage="TUIApp",
            payload=payload,
            session_id=self._session.session_id,
        ))
        if provider_cfg is not None:
            self._session.bus.emit(RuntimeEvent(
                type=EventType.PROVIDER_SWAPPED,
                stage="TUIApp",
                payload={
                    "from_provider": old_pcfg.name, "from_model": old_pcfg.model,
                    "to_provider": provider_cfg.name, "to_model": provider_cfg.model,
                },
                session_id=self._session.session_id,
            ))

        lineage = {
            "resumed_from": old_sid,
            "branched_at_turn": n,
            "restored_message_count": len(messages),
        }
        if retry_of_turn is not None:
            lineage["retry_of_turn"] = retry_of_turn
        if provider_cfg is not None:
            lineage["provider_override"] = {
                "name": provider_cfg.name, "model": provider_cfg.model,
            }
        # Stamp meta.json NOW (child's on_session_start already wrote it), so
        # a tab that stays open — or a hard kill — still preserves lineage.
        # The recorder rewrites meta at on_session_end without these fields,
        # so re-stamp then too (self._pending_meta, applied by
        # _end_session_and_stamp). The session.branched EVENT is the true
        # record either way; this keeps the derived meta honest live.
        from arc.cli.wiring import stamp_session_meta
        stamp_session_meta(self._paths.sessions_dir, self._session.session_id, lineage)
        self._pending_meta = dict(lineage)

        self._console.print(render.render_branch_notice(
            old_sid, n, self._session.session_id, len(messages),
        ))
        if provider_cfg is not None:
            self._console.print(
                f"[arc.brand]⑇[/arc.brand] model swap: "
                f"[arc.dim]{old_pcfg.name}/{old_pcfg.model} →[/arc.dim] "
                f"[arc.info]{provider_cfg.name}/{provider_cfg.model}[/arc.info]"
            )
        return True

    def _end_session_and_stamp(self, tab: _Tab | None = None) -> None:
        """End a tab's session, then apply its pending lineage stamps
        (must come after end — the recorder rewrites meta.json there)."""
        tab = tab if tab is not None else self._tabs[self._focus]
        sid = tab.session.session_id
        tab.session.end()
        if tab.pending_meta and self._paths is not None:
            from arc.cli.wiring import stamp_session_meta
            stamp_session_meta(self._paths.sessions_dir, sid, tab.pending_meta)
        tab.pending_meta = None

    def _close_focused_tab(self) -> None:
        """End the focused tab's session; focus falls to the last remaining
        tab (the parent, in the common branch-then-close flow)."""
        closing = self._tabs[self._focus]
        self._end_session_and_stamp(closing)
        self._tabs.remove(closing)
        self._focus = len(self._tabs) - 1
        self._console.print(
            f"[arc.dim]tab closed ({closing.session.session_id}) — back to "
            f"{self._session.session_id}[/arc.dim]"
        )

    def _handle_tab(self, text: str) -> None:
        """`/tab` — list tabs. `/tab N` — focus tab N (1-based)."""
        parts = text.split()
        if len(parts) == 1:
            for i, tab in enumerate(self._tabs):
                marker = "*" if i == self._focus else " "
                branch = ""
                if tab.pending_meta and "branched_at_turn" in tab.pending_meta:
                    branch = f"  ⑂{tab.pending_meta['branched_at_turn']}"
                self._console.print(
                    f"[arc.dim]{marker}[/arc.dim] {i + 1}: "
                    f"[arc.info]{tab.session.session_id}[/arc.info]"
                    f"[arc.dim]{branch}  turn {tab.turn_count}[/arc.dim]"
                )
            return
        try:
            i = int(parts[1]) - 1
        except ValueError:
            self._console.print(f"[arc.error]/tab: not a tab number: {parts[1]}[/arc.error]")
            return
        if not 0 <= i < len(self._tabs):
            self._console.print(
                f"[arc.error]/tab: no tab {parts[1]} (1-{len(self._tabs)})[/arc.error]")
            return
        if i == self._focus:
            self._console.print("[arc.dim]already on that tab[/arc.dim]")
            return
        self._rewind_target = None  # an armed rewind is tab-local intent
        self._focus = i
        self._console.print(
            f"[arc.dim]── tab {i + 1}:[/arc.dim] "
            f"[arc.info]{self._session.session_id}[/arc.info]"
        )
        # Re-orient: print the tail of this tab's conversation (its own
        # recording; inherited prefix turns live in the parent's tab).
        if self._paths is not None:
            from arc.replay.compare import extract_turns
            turns = extract_turns(self._paths.sessions_dir / self._session.session_id)
            for t in turns[-2:]:
                self._console.print(render.render_turn_card(t, len(turns)))

    def _recorded_user_input(self, source_dir, turn_n: int) -> str | None:
        """The user input of completed turn `turn_n` (1-based), from events."""
        import json
        events_path = source_dir / "events.jsonl"
        if not events_path.is_file():
            return None
        inputs: list[str] = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") == EventType.TURN_STARTED:
                ui = e.get("content", {}).get("user_input")
                if ui:
                    inputs.append(ui)
        if len(inputs) < turn_n:
            return None
        return inputs[turn_n - 1]

    def _handle_replay_menu(self) -> None:
        """Drop into the 0019 replay menu mid-session.

        Spawns `arc replay` as a subprocess so the menu's prompt_toolkit
        screen doesn't conflict with the live TUI.  Returns the user to
        the running session when done.
        """
        import os
        import subprocess

        from arc.bootstrap import resolve_home
        home = resolve_home()
        argv = [sys.executable, "-m", "arc.cli", "--home", str(home), "replay"]
        self._console.print("[arc.dim]launching replay menu (this session continues after)…[/arc.dim]")
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
            f"[arc.dim]conversation cleared ({n} messages removed; session continues)[/arc.dim]"
        )

    def _handle_sessions_list(self) -> None:
        """Render the sessions index as a Rich table inline."""
        # Find ARC_HOME via the recorder plugin's session_dir parent (the
        # cleanest way to discover it without re-resolving env vars).
        sessions_dir = self._find_sessions_dir()
        if sessions_dir is None:
            self._console.print("[arc.dim]/sessions: could not locate sessions directory[/arc.dim]")
            return
        index_path = sessions_dir / "index.jsonl"
        if not index_path.exists():
            self._console.print("[arc.dim]no sessions recorded yet[/arc.dim]")
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
        """Set a two-stage SIGINT (Ctrl+C) handler for when the agent is working.

        Returns the previous handler so we can restore it on exit.

        Stage 1 — a sub-agent is running: cancel it (trip its cancel_flag; the
        child stops at its next iteration boundary and the parent resumes).
        Stage 2 — no sub-agent (or it's already cancelling): pause the turn.

        Only takes effect when prompt_toolkit isn't actively reading input.
        While reading, prompt_toolkit intercepts Ctrl+C as a key and raises
        KeyboardInterrupt — that path already exits the TUI cleanly.
        """
        import signal

        def _handler(signum, frame):
            try:
                from arc.runtime.subagents import cancel as _cancel
                if _cancel.cancel_active():
                    return  # stage 1: cancelled the running sub-agent
            except Exception:
                pass
            # stage 2: bail the turn (pause), or default-bail if no pause plugin.
            # Resolved at fire time — self._session may have been rebuilt by
            # a /rewind branch since the handler was installed (0026).
            pause_plugin = self._find_pause_plugin()
            if pause_plugin is not None:
                try:
                    pause_plugin.request_pause()
                except Exception:
                    pass  # last thing we want is a signal handler raising
            else:
                raise KeyboardInterrupt

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

        # Tab-completion for slash commands
        slash_completer = WordCompleter(
            ["/help", "/exit", "/quit", "/clear", "/sessions", "/replay",
             "/rewind", "/retry", "/model", "/tab"],
            ignore_case=True,
        )

        # alt+1..9 → switch tabs (0026 phase d). Submitting "/tab N" through
        # the normal input path keeps one switching code path.
        from prompt_toolkit.key_binding import KeyBindings
        kb = KeyBindings()
        for _i in range(1, 10):
            @kb.add("escape", str(_i))
            def _switch(event, _n=_i):
                event.app.current_buffer.text = f"/tab {_n}"
                event.app.current_buffer.validate_and_handle()

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

        # Pull bottom-toolbar and toolbar.* style classes from the active theme.
        # See arc/tui/themes/ — every theme provides these so swapping themes
        # restyles the toolbar without touching this file.
        toolbar_style = _active_theme().pt_style

        pt_session = PromptSession(
            history=history,
            completer=slash_completer,
            complete_while_typing=False,  # only on Tab; avoids interrupting input
            bottom_toolbar=bottom_toolbar,
            style=toolbar_style,
            key_bindings=kb,
        )
        patch_ctx = patch_stdout(raw=True)
        patch_ctx.__enter__()
        # Note: we never exit patch_stdout — it's bound to the lifetime of
        # the TUI app. The CLI process exits when the loop ends.


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

        # armed rewind target (0026)
        if self._rewind_target is not None:
            yield ("class:toolbar.turn", f"⑂ branch@{self._rewind_target} ")
            yield ("class:toolbar.sep", "· ")

        # tab strip (0026 phase d) — only once a branch opened a second tab
        if len(self._tabs) > 1:
            strip = " ".join(
                f"{i + 1}{'*' if i == self._focus else ''}"
                for i in range(len(self._tabs))
            )
            yield ("class:toolbar.turn", f"tabs {strip} ")
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
