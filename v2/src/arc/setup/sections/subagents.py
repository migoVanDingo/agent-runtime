"""Sub-agents section — first interactive UI for sub-agent specs.

Lists every spec the registry knows about (built-in, plugin-shipped,
config-defined). Space toggles enablement; Enter saves the diff via the
comment-preserving writer.
"""
from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from arc.setup.sections import Section


_state: dict = {"rows": [], "focus": 0, "msg": ""}


def _load_rows(ctx) -> list[dict]:
    """Build rows from the sub-agent registry + current config overrides."""
    try:
        from arc.config import load
        from arc.runtime.subagents.registry import SubAgentRegistry
    except Exception:
        return []

    try:
        cfg = load(ctx.config_path)
    except Exception:
        return []

    try:
        reg = SubAgentRegistry(arc_home=ctx.home)
        reg.discover(cfg.subagents.as_overrides())
    except Exception:
        return []

    overrides = cfg.subagents.as_overrides()
    rows = []
    for name, spec in reg.all_specs().items():
        # `enabled` in overrides wins; spec default otherwise
        enabled = overrides.get(name, {}).get(
            "enabled", reg.is_enabled(name),
        )
        source = getattr(spec, "source", "builtin") or "builtin"
        provider = getattr(spec, "provider", "—") or "—"
        model = getattr(spec, "model", "—") or "—"
        rows.append({
            "name": name, "enabled": bool(enabled),
            "source": source, "provider": provider, "model": model,
        })
    rows.sort(key=lambda r: r["name"])
    return rows


def _format_text(ctx) -> list:
    out: list = []
    rows = _state["rows"]
    if not rows:
        out.append(("class:hub.dim",
                    "  no sub-agent specs discovered.\n"
                    "  See _design/0020-subagent-dispatch.md and the\n"
                    "  `arc-video-sub-agent` package for an example.\n"))
        return out

    out.append(("class:hub.dim", "  space toggle  ⏎ save\n"))
    out.append(("", "\n"))
    for i, r in enumerate(rows):
        mark = "●" if r["enabled"] else "○"
        mark_style = "class:arc.success" if r["enabled"] else "class:hub.dim"
        line_style = "class:hub.sidebar.item.selected" if i == _state["focus"] else "class:hub.value"
        out.append((mark_style, f"  {mark} "))
        out.append((line_style, f"{r['name']:<24}"))
        out.append(("class:hub.dim", f" {r['source']:<10} {r['provider']}/{r['model']}\n"))

    if _state["msg"]:
        out.append(("", "\n"))
        style = "class:arc.error" if _state["msg"].startswith("error") else "class:arc.success"
        out.append((style, f"  {_state['msg']}\n"))
    return out


def build(ctx) -> Section:
    _state["rows"] = _load_rows(ctx)
    _state["focus"] = 0
    _state["msg"] = ""

    kb = _build_keybindings(ctx)

    control = FormattedTextControl(
        lambda: _format_text(ctx),
        focusable=True,
        key_bindings=kb,
        show_cursor=False,
    )
    container = Window(content=control, style="class:hub.content")

    def summary() -> str:
        rows = _state["rows"]
        if not rows:
            return "none discovered"
        enabled = sum(1 for r in rows if r["enabled"])
        return f"{enabled} of {len(rows)} enabled"

    def on_enter() -> None:
        _state["rows"] = _load_rows(ctx)
        _state["focus"] = 0
        _state["msg"] = ""

    return Section(
        name="subagents",
        title="Sub-agents",
        summary=summary,
        container=container,
        focusable=True,
        on_enter=on_enter,
    )


def _build_keybindings(ctx) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        rows = _state["rows"]
        if rows:
            _state["focus"] = (_state["focus"] - 1) % len(rows)
            if ctx.request_redraw:
                ctx.request_redraw()

    @kb.add("down")
    def _(event):
        rows = _state["rows"]
        if rows:
            _state["focus"] = (_state["focus"] + 1) % len(rows)
            if ctx.request_redraw:
                ctx.request_redraw()

    @kb.add("space")
    def _(event):
        rows = _state["rows"]
        if not rows:
            return
        r = rows[_state["focus"]]
        r["enabled"] = not r["enabled"]
        if ctx.request_redraw:
            ctx.request_redraw()

    @kb.add("enter")
    def _(event):
        try:
            _save(ctx)
            _state["msg"] = "saved"
        except Exception as exc:
            _state["msg"] = f"error: {exc}"
        if ctx.request_redraw:
            ctx.request_redraw()

    return kb


def _save(ctx) -> None:
    """Write subagent enable/disable diffs via the existing writer."""
    from arc.setup.writer import write_subagent_enablement
    for r in _state["rows"]:
        write_subagent_enablement(
            ctx.config_path, name=r["name"], enabled=r["enabled"],
        )
