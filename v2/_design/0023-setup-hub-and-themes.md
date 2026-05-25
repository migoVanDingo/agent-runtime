# 0023 — Setup hub and themes

## Motivation

`arc` has grown a handful of one-shot interactive commands, each implemented
as an isolated stack of `prompt_toolkit.shortcuts.{radiolist,checkboxlist,
input,yes_no}_dialog` calls:

| Command | Today |
|---|---|
| `arc setup` | provider → model → confirm (`setup/picker.py`) |
| `arc plugins` | single checkbox list (`setup/plugin_menu.py`) |
| `arc replay` (no args) | session → mode → provider → model → batch (`tui/replay_menu.py`) |
| `arc subagents` | non-interactive only — CLI prints "interactive TUI menu is not yet implemented" |
| `arc llm` | non-interactive subcommands only |
| `arc wipe` | flag-driven, no menu |
| `arc config show/path` | print only |

Two problems:

1. **No cross-navigation.** Each command is a dead end. Want to flip a
   plugin then change provider? Two separate invocations. Want to wipe
   sessions before a replay run? Three separate invocations. Discovery is
   bad — a new user doesn't know `arc subagents` or `arc llm` exist.
2. **Default `prompt_toolkit` chrome.** Gray boxes on a blue title bar
   with no theming hook. `config.yml` has had `tui.theme: default` since
   day one (`defaults.py:221`) but it's unused — Rich rendering hardcodes
   colors and `code_theme="monokai"`, dialog calls pass no `style=`.

This phase ships:

1. A **single navigable setup hub** (`arc setup`) — sidebar + content
   pane — that contains every interactive config surface and exposes
   sub-agents interactively for the first time.
2. A **theme system** that restyles both the hub and the live TUI session.
   Colors only; layout, behavior, and chrome positions don't change.

Existing CLI shortcuts (`arc plugins`, `arc replay`, `arc subagents`,
`arc llm`, `arc wipe`) remain — they open the hub pre-focused on their
section.

---

## Scope

In:
- New `arc/tui/themes/` package — `Theme` dataclass, registry, 3–5 built-ins
- Theme wiring through Rich `Console`, `Markdown` code blocks, prompt_toolkit
  dialogs, and the bottom toolbar
- New `arc/setup/hub.py` — single `prompt_toolkit.Application` with sidebar
  navigation, hosting every section
- A new interactive sub-agents section (parity with existing
  `list/show/enable/disable` CLI plus toggle)
- A new LLM-server section (start/stop/restart/status — wraps `arc llm`)
- A new wipe/reset section (interactive checkbox over wipe targets)
- A new themes section with live preview
- A read-only status/diagnostics section
- Read-only config viewer (path + raw YAML)
- Tests covering theme registry, hub routing, and each section's smoke path

Out:
- User-droppable theme files (`~/.arc/themes/*.py` or similar) — built-in
  themes only for v1 of this phase
- Restyling individual events.jsonl output or `arc show` / `arc log`
  (those are dump-to-stdout and not interactive)
- Restructuring the live TUI session's *layout* (panels, toolbar position,
  prompt position) — the user is happy with the existing TUI
- Migration of `arc bootstrap` into the hub — bootstrap creates the home
  the hub depends on; it stays a separate command
- A general config editor (everything still routes through `config.yml`
  via the comment-preserving writer in `setup/writer.py`)

---

## Theme system

### Surface

A theme is a `Theme` dataclass with exactly three fields and a name/desc:

```python
@dataclass(frozen=True)
class Theme:
    name: str
    description: str
    pt_style: prompt_toolkit.styles.Style   # hub, dialogs, toolbar
    rich_theme: rich.theme.Theme            # named styles for render.py
    code_theme: str                         # pygments name for Markdown
```

That's the entire contract. No layout knobs, no spacing, no glyphs —
swapping a theme can never change *what* is on screen, only colors.

### Built-in themes

Shipping for v1:

| Name | Feel |
|---|---|
| `default` | What we have today — current ad-hoc colors, no visible change when selected |
| `dracula` | Dark purple/pink |
| `solarized-dark` | Muted dark, classic |
| `gruvbox` | Warm, high-contrast dark |
| `mono` | Terminal-default colors only (dumb-terminal / accessibility fallback) |

Each lives in `arc/tui/themes/<name>.py` and exports a `THEME: Theme`
constant. `arc/tui/themes/__init__.py` builds a `REGISTRY: dict[str, Theme]`
via explicit imports — no auto-discovery, no plugin entry point. Keeps
the surface small and grep-able.

### Named styles

The Rich theme defines a small, fixed namespace consumed by `render.py`:

| Style key | Used for |
|---|---|
| `arc.user` | user turn prompt prefix and echo |
| `arc.assistant` | assistant text body |
| `arc.thinking` | extended-thinking blocks |
| `arc.tool.name` | tool call header |
| `arc.tool.output` | collapsed/truncated tool output |
| `arc.error` | exceptions, validation errors |
| `arc.dim` | timestamps, ids, secondary metadata |
| `arc.toolbar.label` / `arc.toolbar.value` / `arc.toolbar.cost` | bottom toolbar segments |
| `arc.accent` | highlights, focus indicator in hub |

`render.py` is updated to address these named styles instead of literal
color strings. The Pygments `code_theme` is read from the active theme
and passed to every `Markdown(...)` constructor.

### prompt_toolkit style

The `pt_style` covers the standard prompt_toolkit class names used by
dialogs (`dialog`, `dialog.body`, `dialog frame.label`,
`radio-selected`, `button.focused`) plus a small set of hub-specific
classes (`hub.sidebar`, `hub.sidebar.selected`, `hub.content`,
`hub.footer`).

### Application points

Touched files:

- `arc/tui/app.py` — `Console(theme=theme.rich_theme)`; toolbar segments
  use style classes
- `arc/tui/render.py` — replace hardcoded colors and `code_theme="monokai"`
  with the active theme
- `arc/setup/picker.py`, `arc/setup/plugin_menu.py`, `arc/tui/replay_menu.py`
  — every `*_dialog(...)` call gains `style=theme.pt_style`
- `arc/setup/hub.py` (new) — `Application(style=theme.pt_style)`

A small `arc/tui/themes/active.py` resolves the active theme from config
once at process start and caches it. `load_theme(name) -> Theme` falls back
to `default` with a stderr warning if the name is unknown — bad config
never crashes the TUI.

---

## Setup hub

### Layout

Sidebar + content pane. Sidebar is always visible (so the user can see
what else exists); content swaps when a section is opened.

```
┌─ arc setup ─────────────────────────────────────────┐
│                                                     │
│  > Provider & Model    │  Provider:  anthropic      │
│    Plugins             │  Model:     claude-opus-4-7│
│    Sub-agents          │                            │
│    Replay              │  [ Change provider ]       │
│    LLM Server          │  [ Change model    ]       │
│    Themes              │                            │
│    Status              │  Last edit: 2 days ago     │
│    Wipe / Reset        │                            │
│    Config              │                            │
│                                                     │
│  ↑/↓ navigate  ⏎ open  esc quit                     │
└─────────────────────────────────────────────────────┘
```

Built as a single `prompt_toolkit.Application` (full-screen),
not as chained modal dialogs. Sections are `Container`s the hub swaps
into the right-hand `HSplit`.

### Sections

| # | Section | Source | Notes |
|---|---|---|---|
| 1 | Provider & Model | wraps `setup/picker.py` | Opens existing radio flow inside the hub frame; on save, returns to hub |
| 2 | Plugins | wraps `setup/plugin_menu.py` | Existing checkbox list reused |
| 3 | Sub-agents | new view; backed by `arc subagents` CLI helpers | First interactive UI for this |
| 4 | Replay | wraps `tui/replay_menu.py` | Existing multi-step flow reused |
| 5 | LLM Server | new view; wraps `arc/llm/commands.py` | start/stop/restart/status |
| 6 | Themes | new view | Radio list with live preview |
| 7 | Status | new view, read-only | ARC_HOME path, provider/model, llama-server status, plugin count, version |
| 8 | Wipe / Reset | new view; wraps `arc/wipe.py` | Checkbox of targets + confirm |
| 9 | Config | new view, read-only | Path + raw YAML viewer (scrollable) |

Sections live under `arc/setup/sections/`. Each is a module exporting
`build_section(ctx) -> Section`, where `Section` is a small protocol:

```python
class Section(Protocol):
    title: str
    summary: Callable[[], str]   # one line shown in the sidebar hint area
    container: prompt_toolkit.layout.Container
    on_enter: Callable[[], None] | None
    on_leave: Callable[[], None] | None
```

The hub stays under ~300 LoC; each section is small (50–150 LoC) and
testable in isolation.

### CLI integration

- `arc setup` → opens the hub on section 1
- `arc setup --provider X --model Y` → unchanged (non-interactive path
  preserved verbatim)
- `arc plugins` → opens the hub on section 2
- `arc plugins list` → unchanged (non-interactive)
- `arc subagents` (no subcommand, new) → opens the hub on section 3
- `arc replay` (no session id) → opens the hub on section 4
- `arc llm` (no subcommand, new) → opens the hub on section 5
- `arc wipe` (no targets) → opens the hub on section 8

All existing flag-driven invocations keep their non-interactive contract.

### Keybinds

- `↑ / ↓` — sidebar navigation
- `⏎` — open section in content pane (focus moves right)
- `esc` — when focus is in a section, returns to sidebar; when focus is in
  sidebar, quits the hub
- `q` from sidebar — quit
- `?` — overlay with all keybinds (built lazily, lives on hub itself)

### State

The hub is stateless beyond the active section index. Each section reads
config fresh on enter and writes through the existing comment-preserving
writer at `setup/writer.py`. No diff is held in memory; on-disk YAML
remains the single source of truth, identical to current behavior.

---

## Backwards compatibility

- `tui.theme` config key was always present; default is `default`. Existing
  configs keep loading. Unknown names log a warning and fall back.
- Every flag-driven CLI form (`arc setup --provider`, `arc plugins list`,
  `arc replay <id>`, `arc llm start <id>`, `arc wipe --sessions`) preserves
  its current exact behavior and exit codes — scripts don't break.
- Headless contexts (`stdin` not a TTY) never open the hub; commands that
  used to error with "interactive required" still do.
- `prompt_toolkit` dialog wrappers retain their function signatures, so
  any test or external caller that imports `_pick_provider` etc. keeps
  working — the only change is they now accept (and default to) the
  active theme's style.

---

## Testing

- `tests/unit/test_themes.py` — registry contains expected names; every
  theme produces a non-empty `pt_style` and `rich_theme`; unknown name
  falls back to `default` with a stderr warning
- `tests/unit/test_setup_hub_routing.py` — hub builds; each section's
  `build_section` constructs a non-empty container; CLI shortcuts route
  to the correct section index
- `tests/unit/test_setup_sections_*.py` — one per new section (sub-agents,
  llm server, themes, status, wipe, config viewer), driven by injected
  fakes/paths, no real prompt_toolkit event loop required
- Existing tests for `picker.py`, `plugin_menu.py`, `replay_menu.py` keep
  passing — those modules' public functions are unchanged

---

## Phases

1. **Theme primitives + wiring** — `Theme`, registry, 3 themes; wire into
   render.py, app.py, and the existing three dialog files. Visible win:
   existing menus pick up theme colors. (~1 day)
2. **Hub shell** — sidebar + content frame, section protocol, status +
   config viewer + themes sections as the first three (read-only / new).
   Hub opens on `arc setup`. (~1 day)
3. **Migrate existing flows** — Provider & Model, Plugins, Replay sections
   wrapped. CLI shortcuts route into the hub. (~1 day)
4. **New interactive sections** — Sub-agents, LLM Server, Wipe / Reset.
   (~1 day)
5. **Polish + docs** — keybind overlay, `_architecture/` updates, theme
   gallery in the README. (~½ day)

Each phase is independently shippable and tested. Phase 1 alone removes
the "Reader Rabbit" complaint even before the hub exists.
