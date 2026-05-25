"""LLM Server section — start/stop/restart/status for llama-server.

Wraps arc/llm/commands.py. Shows current process state and a list of
registered models. Hotkeys: s start (focused row), S stop, r restart.
"""
from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from arc.setup.sections import Section


_state: dict = {"models": [], "focus": 0, "msg": ""}


def _refresh(ctx) -> None:
    _state["models"] = _list_models(ctx)
    _state["focus"] = 0


def _list_models(ctx) -> list[dict]:
    try:
        from arc.llm.registry import load_registry, RegistryError
    except Exception:
        return []
    try:
        reg = load_registry(ctx.llm_servers_path)
    except (RegistryError, Exception):
        return []
    out: list[dict] = []
    try:
        for m in reg.models:
            out.append({"id": getattr(m, "id", str(m)), "label": getattr(m, "label", "")})
    except Exception:
        pass
    return out


def _current_status(ctx):
    try:
        from arc.bootstrap import paths_for
        from arc.llm.process import status as proc_status
        return proc_status(llm_dir=paths_for(ctx.home).llm_dir)
    except Exception:
        return None


def _format_text(ctx) -> list:
    out: list = []
    cur = _current_status(ctx)
    out.append(("class:hub.accent", "  current server\n"))
    out.append(("class:hub.dim",   "  ──────────────\n"))
    if cur is None:
        out.append(("class:hub.dim", "  (not running)\n"))
    else:
        out.append(("class:hub.label", "  status  "))
        out.append(("class:arc.success", "running\n"))
        out.append(("class:hub.label", "  pid     "))
        out.append(("class:hub.value", f"{cur.pid}\n"))
        out.append(("class:hub.label", "  model   "))
        out.append(("class:hub.value", f"{cur.model_id}\n"))

    out.append(("", "\n"))
    out.append(("class:hub.accent", "  registered models\n"))
    out.append(("class:hub.dim",   "  ─────────────────\n"))
    models = _state["models"]
    if not models:
        out.append(("class:hub.dim", "  none — populate $ARC_HOME/llm_servers.yml\n"))
    for i, m in enumerate(models):
        line_style = "class:hub.sidebar.item.selected" if i == _state["focus"] else "class:hub.value"
        out.append((line_style, f"  {m['id']:<32}"))
        out.append(("class:hub.dim", f"{m['label']}\n"))

    out.append(("", "\n"))
    out.append(("class:hub.dim", "  ↑↓ focus  s start  S stop  r restart  l logs\n"))
    if _state["msg"]:
        out.append(("", "\n"))
        style = "class:arc.error" if _state["msg"].startswith("error") else "class:arc.success"
        out.append((style, f"  {_state['msg']}\n"))
    return out


def build(ctx) -> Section:
    _refresh(ctx)

    kb = _build_keybindings(ctx)

    control = FormattedTextControl(
        lambda: _format_text(ctx),
        focusable=True,
        key_bindings=kb,
        show_cursor=False,
    )
    container = Window(content=control, style="class:hub.content")

    def summary() -> str:
        cur = _current_status(ctx)
        if cur is None:
            return "not running"
        return f"running: {cur.model_id}"

    def on_enter():
        _refresh(ctx)
        _state["msg"] = ""

    return Section(
        name="llm",
        title="LLM Server",
        summary=summary,
        container=container,
        focusable=True,
        on_enter=on_enter,
    )


def _build_keybindings(ctx) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        if _state["models"]:
            _state["focus"] = (_state["focus"] - 1) % len(_state["models"])
            if ctx.request_redraw:
                ctx.request_redraw()

    @kb.add("down")
    def _(event):
        if _state["models"]:
            _state["focus"] = (_state["focus"] + 1) % len(_state["models"])
            if ctx.request_redraw:
                ctx.request_redraw()

    @kb.add("s")
    def _(event):
        models = _state["models"]
        if not models:
            return
        mid = models[_state["focus"]]["id"]
        _do(ctx, "start", mid)

    @kb.add("S")
    def _(event):
        _do(ctx, "stop", None)

    @kb.add("r")
    def _(event):
        models = _state["models"]
        if not models:
            return
        mid = models[_state["focus"]]["id"]
        _do(ctx, "restart", mid)

    @kb.add("l")
    def _(event):
        _do(ctx, "logs", None)

    return kb


def _do(ctx, action: str, model_id: str | None) -> None:
    """Dispatch a server action. Result message goes to _state['msg']."""
    try:
        from arc.bootstrap import paths_for
        from arc.llm import commands as llm_cmd
        paths = paths_for(ctx.home)
        if action == "start":
            rc = llm_cmd.start_server(paths, model_id)
            _state["msg"] = f"started {model_id}" if rc == 0 else f"error: start failed (rc={rc})"
        elif action == "stop":
            rc = llm_cmd.stop_server(paths)
            _state["msg"] = "stopped" if rc == 0 else f"error: stop failed (rc={rc})"
        elif action == "restart":
            rc = llm_cmd.restart_server(paths, model_id)
            _state["msg"] = f"restarted {model_id}" if rc == 0 else f"error: restart failed (rc={rc})"
        elif action == "logs":
            # Dump logs to stdout — needs the full-screen Application torn down.
            def _go():
                llm_cmd.show_logs(paths, tail=50)
                try:
                    input("\n(press enter to return)")
                except (EOFError, KeyboardInterrupt):
                    pass
            if ctx.run_modal is not None:
                ctx.run_modal(_go)
            _state["msg"] = ""
    except Exception as exc:
        _state["msg"] = f"error: {exc}"
    if ctx.request_redraw:
        ctx.request_redraw()
