"""arc setup — the unified configuration hub.

A single prompt_toolkit Application that hosts every interactive config
surface (provider/model, plugins, sub-agents, replay, llm server, themes,
status, wipe, config viewer). Sidebar always visible; content swaps when
a section is opened.

Keybinds:
  ↑/↓     navigate sidebar
  ⏎       open section in content pane (focus moves right)
  esc     in content: return to sidebar.  In sidebar: exit hub AND launch
          an arc session (the "done configuring, let's use it" path).
  q       quit hub back to the shell — no session launch.
  ctrl-c  same as q.
  ?       toggle keybind overlay

run_hub() returns a HubResult so the caller (cli.py) can decide whether
to drop into _cmd_interactive after the hub exits.

See _design/0023-setup-hub-and-themes.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    HSplit,
    Layout,
    VSplit,
    Window,
    WindowAlign,
)
from prompt_toolkit.layout.containers import Float, FloatContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.widgets import Frame

from arc.tui.themes import active as _active_theme


# ── Hub context ────────────────────────────────────────────────────────────


@dataclass
class HubContext:
    """Bundle of paths + state passed to every section's build()."""
    home: Path
    config_path: Path
    catalog_path: Path
    llm_servers_path: Path

    # Set by the hub at startup so sections can request a redraw or quit
    request_redraw: Callable[[], None] | None = None
    request_quit: Callable[[], None] | None = None
    # Run a modal callable (e.g. a nested radiolist_dialog or a stdout dump
    # that needs full-screen restored). The hub exits the live Application,
    # runs the callable, then re-launches itself focused on the same section.
    # Use this instead of any Application.run_in_terminal call — modal
    # prompt_toolkit dialogs spin up their own Application and can't nest.
    run_modal: Callable[[Callable[[], None]], None] | None = None

    # ── Cached config (avoid re-parsing YAML per redraw) ──────────────────
    # prompt_toolkit calls every FormattedTextControl thunk on each render
    # frame; with up to nine sections reading config each frame, parsing on
    # every call adds visible latency to arrow-key nav. invalidate_cache()
    # is called after any modal that may have rewritten config.
    _config_cache: object = None
    _config_cache_mtime: float = 0.0

    def load_config(self):
        """Return the parsed Config, cached by file mtime."""
        from arc.config import load
        try:
            mtime = self.config_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if self._config_cache is None or mtime != self._config_cache_mtime:
            self._config_cache = load(self.config_path)
            self._config_cache_mtime = mtime
        return self._config_cache

    def invalidate_config(self) -> None:
        self._config_cache = None
        self._config_cache_mtime = 0.0


# ── Hub class ──────────────────────────────────────────────────────────────


class Hub:
    """Application wrapper. Builds the layout, owns navigation state."""

    def __init__(self, ctx: HubContext, initial_section: str | None = None) -> None:
        self.ctx = ctx
        self.ctx.request_redraw = self._request_redraw
        self.ctx.request_quit = self._request_quit
        self.ctx.run_modal = self._schedule_modal
        self._sections = self._build_sections()
        self._index = self._find_initial_index(initial_section)
        self._focus_in_content = False
        self._show_help = False
        self._app: Application | None = None
        self._pending_modal: Callable[[], None] | None = None
        self._should_quit = False
        self._launch_session = False

    # ── Section construction ───────────────────────────────────────────────

    def _build_sections(self):
        """Eager imports + build(). Order matters — defines sidebar order."""
        from arc.setup.sections import (
            config_viewer,
            llm_server,
            plugins as _plugins,
            provider_model,
            replay as _replay,
            status,
            subagents as _subagents,
            themes as _themes,
            wipe as _wipe,
        )
        builders = [
            provider_model.build,
            _plugins.build,
            _subagents.build,
            _replay.build,
            llm_server.build,
            _themes.build,
            status.build,
            _wipe.build,
            config_viewer.build,
        ]
        return [b(self.ctx) for b in builders]

    def _find_initial_index(self, name: str | None) -> int:
        if name is None:
            return 0
        for i, s in enumerate(self._sections):
            if s.name == name:
                return i
        return 0

    # ── Hub-level actions ──────────────────────────────────────────────────

    def _request_redraw(self) -> None:
        if self._app is not None:
            self._app.invalidate()

    def _request_quit(self) -> None:
        self._should_quit = True
        if self._app is not None:
            self._app.exit()

    def _schedule_modal(self, fn: Callable[[], None]) -> None:
        """Section asked to run a modal action. Exit the hub Application;
        the run-loop will execute the callable, then re-enter the hub on
        the same section."""
        self._pending_modal = fn
        if self._app is not None:
            self._app.exit()

    def _move(self, delta: int) -> None:
        n = len(self._sections)
        self._index = (self._index + delta) % n
        self._request_redraw()

    def _enter_section(self) -> None:
        sec = self._sections[self._index]
        if sec.on_enter is not None:
            sec.on_enter()
        if sec.focusable and self._app is not None:
            # Try to move focus into the content container
            try:
                self._app.layout.focus(sec.container)
                self._focus_in_content = True
            except Exception:
                # Some containers (read-only Windows) can't take focus —
                # that's fine, just leave focus in sidebar
                pass
        self._request_redraw()

    def _leave_section(self) -> None:
        sec = self._sections[self._index]
        if sec.on_leave is not None:
            sec.on_leave()
        self._focus_in_content = False
        if self._app is not None:
            try:
                self._app.layout.focus(self._sidebar_window)
            except Exception:
                pass
        self._request_redraw()

    # ── Layout ─────────────────────────────────────────────────────────────

    def _sidebar_text(self):
        out = []
        out.append(("class:hub.title", " arc setup\n"))
        out.append(("class:hub.dim", " ─────────\n"))
        for i, sec in enumerate(self._sections):
            if i == self._index:
                out.append(("class:hub.sidebar.item.selected", f" › {sec.title:<18}\n"))
            else:
                out.append(("class:hub.sidebar.item", f"   {sec.title:<18}\n"))
        out.append(("", "\n"))
        out.append(("class:hub.dim", " ↑↓ nav  ⏎ open\n"))
        out.append(("class:hub.accent", " esc  start session\n"))
        out.append(("class:hub.dim", " q    quit to shell\n"))
        out.append(("class:hub.dim", " ?    help\n"))
        return out

    def _content_text(self):
        """Header above the active section's container."""
        sec = self._sections[self._index]
        out = [
            ("class:hub.section.title", f" {sec.title}\n"),
            ("class:hub.dim", f" {sec.summary()}\n"),
            ("", "\n"),
        ]
        return out

    def _footer_text(self):
        scope = "content" if self._focus_in_content else "sidebar"
        theme_name = _active_theme().name
        return [
            ("class:hub.footer", f" focus: {scope}   theme: {theme_name}   "),
            ("class:hub.dim", "ARC_HOME="),
            ("class:hub.footer", f"{self.ctx.home}"),
        ]

    def _build_layout(self) -> Layout:
        sidebar_control = FormattedTextControl(
            self._sidebar_text, focusable=True, show_cursor=False,
        )
        self._sidebar_window = Window(
            content=sidebar_control,
            width=D.exact(24),
            style="class:hub.sidebar",
        )

        header_window = Window(
            content=FormattedTextControl(self._content_text, focusable=False),
            height=D(min=3, max=3),
            style="class:hub.content",
        )

        # The content area dispatches on self._index — we render all sections
        # and conditionally show only the current one. Cheaper than rebuilding
        # the layout on every nav change.
        content_stack = HSplit([
            ConditionalContainer(
                content=sec.container,
                filter=_index_is(self, i),
            )
            for i, sec in enumerate(self._sections)
        ])

        right_pane = HSplit([
            header_window,
            content_stack,
        ], style="class:hub.content")

        body = VSplit([
            self._sidebar_window,
            Window(width=D.exact(1), char="│", style="class:hub.divider"),
            right_pane,
        ])

        footer = Window(
            content=FormattedTextControl(self._footer_text, focusable=False),
            height=D.exact(1),
            style="class:hub.footer",
        )

        root = HSplit([
            body,
            Window(height=D.exact(1), char="─", style="class:hub.divider"),
            footer,
        ])

        # Help overlay (Float)
        help_overlay = Float(
            content=ConditionalContainer(
                content=Frame(
                    body=Window(
                        content=FormattedTextControl(_help_text, focusable=False),
                        height=D(min=10, max=14),
                        width=D(min=48, max=48),
                    ),
                    title="keybinds",
                    style="class:dialog",
                ),
                filter=_help_visible(self),
            ),
            top=2, right=2,
        )

        return Layout(FloatContainer(content=root, floats=[help_overlay]),
                      focused_element=self._sidebar_window)

    # ── Keybindings ────────────────────────────────────────────────────────

    def _build_keybindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("up", filter=_in_sidebar(self))
        def _(event):
            self._move(-1)

        @kb.add("down", filter=_in_sidebar(self))
        def _(event):
            self._move(+1)

        @kb.add("enter", filter=_in_sidebar(self))
        def _(event):
            self._enter_section()

        @kb.add("escape", filter=_in_content(self))
        def _(event):
            self._leave_section()

        @kb.add("escape", filter=_in_sidebar(self))
        def _(event):
            # esc from sidebar = "done configuring, launch the session"
            self._launch_session = True
            self._should_quit = True
            event.app.exit()

        @kb.add("q", filter=_in_sidebar(self))
        def _(event):
            # q = quit cleanly to shell
            self._should_quit = True
            event.app.exit()

        @kb.add("c-c")
        def _(event):
            # ctrl-c = quit cleanly to shell (no session launch)
            self._should_quit = True
            event.app.exit()

        @kb.add("?")
        def _(event):
            self._show_help = not self._show_help
            self._request_redraw()

        return kb

    # ── Run ────────────────────────────────────────────────────────────────

    def run(self) -> "HubResult":
        """Hub event loop. Re-enters itself after each modal action so
        nested dialogs (which need their own Application) can run between
        hub renders without conflict.

        Returns HubResult so the CLI can decide whether to drop into a
        live arc session after the hub exits (esc from sidebar = yes;
        q / ctrl-c = no)."""
        while True:
            layout = self._build_layout()
            kb = self._build_keybindings()
            self._app = Application(
                layout=layout,
                key_bindings=kb,
                full_screen=True,
                mouse_support=False,
                style=_active_theme().pt_style,
            )
            try:
                self._app.run()
            except (KeyboardInterrupt, EOFError):
                return HubResult(rc=0, launch_session=False)
            self._app = None

            if self._should_quit:
                return HubResult(rc=0, launch_session=self._launch_session)
            if self._pending_modal is None:
                # User pressed q / ctrl-c — exit cleanly.
                return HubResult(rc=0, launch_session=False)

            # Run the modal callable with the full-screen Application torn
            # down so it can spawn its own (radiolist_dialog, show_logs, …).
            fn = self._pending_modal
            self._pending_modal = None
            try:
                fn()
            except SystemExit:
                # picker raises SystemExit on abort — treat as cancel
                pass
            except Exception as exc:
                import sys
                sys.stderr.write(f"\nsection action failed: {exc}\n")
            # Config may have changed; sections must see fresh values.
            self.ctx.invalidate_config()
            # Loop re-enters the hub on the same section index.


# ── Filters (prompt_toolkit Conditions) ────────────────────────────────────


def _in_sidebar(hub: "Hub"):
    from prompt_toolkit.filters import Condition
    return Condition(lambda: not hub._focus_in_content)


def _in_content(hub: "Hub"):
    from prompt_toolkit.filters import Condition
    return Condition(lambda: hub._focus_in_content)


def _index_is(hub: "Hub", i: int):
    from prompt_toolkit.filters import Condition
    return Condition(lambda: hub._index == i)


def _help_visible(hub: "Hub"):
    from prompt_toolkit.filters import Condition
    return Condition(lambda: hub._show_help)


def _help_text():
    return [
        ("", "\n"),
        ("class:hub.accent", "  ↑ / ↓"), ("", "   navigate sidebar\n"),
        ("class:hub.accent", "  ⏎     "), ("", "   open section\n"),
        ("class:hub.accent", "  esc   "), ("", "   in section: back to sidebar\n"),
        ("",                "            "), ("class:hub.dim", "in sidebar: start arc session\n"),
        ("class:hub.accent", "  q     "), ("", "   quit hub to shell (no session)\n"),
        ("class:hub.accent", "  ?     "), ("", "   toggle this overlay\n"),
        ("", "\n"),
        ("class:hub.dim", "  ctrl-c quits without launching.\n"),
    ]


# ── Public entry points ────────────────────────────────────────────────────


@dataclass
class HubResult:
    """Outcome of a hub run. CLI inspects launch_session to decide whether
    to drop the user into a live arc session after the hub exits."""
    rc: int
    launch_session: bool


def run_hub(home: Path, initial_section: str | None = None) -> HubResult:
    """Build the hub for an existing ARC_HOME and run it.

    initial_section: name (e.g. "themes") to focus on launch. Defaults to
    the first section (Provider & Model).
    """
    from arc.bootstrap import paths_for
    p = paths_for(home)
    ctx = HubContext(
        home=home,
        config_path=p.config_file,
        catalog_path=p.catalog_file,
        llm_servers_path=p.llm_servers_file,
    )
    return Hub(ctx, initial_section=initial_section).run()


__all__ = ["Hub", "HubContext", "HubResult", "run_hub"]
