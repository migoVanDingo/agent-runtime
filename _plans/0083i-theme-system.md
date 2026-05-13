# 0083i — Theme system + built-in themes

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §5.
> Depends on: **0083f** (Textual skeleton).

## Goal

Implement the TCSS-based theme system:
- `themes/_vars.tcss` — the CSS variable contract all widgets code against
- 7 built-in themes as `.tcss` files
- A `ThemeLoader` that discovers built-in and user themes from `~/.arc/themes/`
- Wire `/theme list` and `/theme <name>` to actually reload the CSS
- Live reload via Textual's stylesheet mechanism

## Files to create / modify

| File | Action |
|------|--------|
| `src/ui/themes/_vars.tcss` | **Create** — CSS variable contract |
| `src/ui/themes/default.tcss` | **Create** — neutral dark theme |
| `src/ui/themes/dracula.tcss` | **Create** — Dracula palette |
| `src/ui/themes/nord.tcss` | **Create** — Nord palette |
| `src/ui/themes/tokyo-night.tcss` | **Create** — Tokyo Night palette |
| `src/ui/themes/gruvbox-dark.tcss` | **Create** — Gruvbox Dark palette |
| `src/ui/themes/solarized-dark.tcss` | **Create** — Solarized Dark palette |
| `src/ui/themes/light.tcss` | **Create** — neutral light theme |
| `src/ui/theme_loader.py` | **Create** — discovery + apply logic |
| `src/ui/app.py` | **Modify** — wire theme loading on startup + reactive theme |
| `src/ui/commands/builtin.py` | **Modify** — wire `/theme` to real loader |

## TCSS variable contract

Every widget must use variables from `_vars.tcss`, never literal hex colors.
This is the invariant that makes theme switching work without touching widget code.

### `src/ui/themes/_vars.tcss`

```css
/* Shared CSS variable contract for all arc-tui themes.
   All variables must be defined in every theme file.
   Widget styles reference only these variables — never literal colors.
*/

/* Backgrounds */
$bg:           #1e1e1e;
$bg-elevated:  #252526;
$surface:      #2d2d30;

/* Text */
$text:         #d4d4d4;
$text-dim:     #858585;

/* Accent / brand */
$primary:      #007acc;
$accent:       #00ff87;

/* Semantic colors */
$success:      #4ec9b0;
$warning:      #dcdcaa;
$error:        #f48771;

/* Structure */
$border:       #3e3e42;
```

### `src/ui/themes/default.tcss`

```css
/* Default dark theme — neutral VS Code-inspired palette. */
$bg:           #1e1e1e;
$bg-elevated:  #252526;
$surface:      #2d2d30;
$text:         #d4d4d4;
$text-dim:     #858585;
$primary:      #007acc;
$accent:       #00ff87;
$success:      #4ec9b0;
$warning:      #dcdcaa;
$error:        #f48771;
$border:       #3e3e42;
```

### `src/ui/themes/dracula.tcss`

```css
/* Dracula theme — https://draculatheme.com */
$bg:           #282a36;
$bg-elevated:  #343746;
$surface:      #44475a;
$text:         #f8f8f2;
$text-dim:     #6272a4;
$primary:      #bd93f9;
$accent:       #50fa7b;
$success:      #50fa7b;
$warning:      #f1fa8c;
$error:        #ff5555;
$border:       #44475a;
```

### `src/ui/themes/nord.tcss`

```css
/* Nord theme — https://www.nordtheme.com */
$bg:           #2e3440;
$bg-elevated:  #3b4252;
$surface:      #434c5e;
$text:         #eceff4;
$text-dim:     #4c566a;
$primary:      #5e81ac;
$accent:       #88c0d0;
$success:      #a3be8c;
$warning:      #ebcb8b;
$error:        #bf616a;
$border:       #4c566a;
```

### `src/ui/themes/tokyo-night.tcss`

```css
/* Tokyo Night theme */
$bg:           #1a1b26;
$bg-elevated:  #24283b;
$surface:      #2f334d;
$text:         #c0caf5;
$text-dim:     #565f89;
$primary:      #7aa2f7;
$accent:       #9ece6a;
$success:      #9ece6a;
$warning:      #e0af68;
$error:        #f7768e;
$border:       #3b4261;
```

### `src/ui/themes/gruvbox-dark.tcss`

```css
/* Gruvbox Dark — https://github.com/morhetz/gruvbox */
$bg:           #282828;
$bg-elevated:  #3c3836;
$surface:      #504945;
$text:         #ebdbb2;
$text-dim:     #928374;
$primary:      #458588;
$accent:       #b8bb26;
$success:      #b8bb26;
$warning:      #fabd2f;
$error:        #fb4934;
$border:       #504945;
```

### `src/ui/themes/solarized-dark.tcss`

```css
/* Solarized Dark — https://ethanschoonover.com/solarized */
$bg:           #002b36;
$bg-elevated:  #073642;
$surface:      #073642;
$text:         #839496;
$text-dim:     #586e75;
$primary:      #268bd2;
$accent:       #2aa198;
$success:      #859900;
$warning:      #b58900;
$error:        #dc322f;
$border:       #073642;
```

### `src/ui/themes/light.tcss`

```css
/* Light theme — neutral light for bright environments. */
$bg:           #ffffff;
$bg-elevated:  #f3f3f3;
$surface:      #e8e8e8;
$text:         #1e1e1e;
$text-dim:     #5a5a5a;
$primary:      #0066cc;
$accent:       #007700;
$success:      #007700;
$warning:      #996600;
$error:        #cc0000;
$border:       #cccccc;
```

## `src/ui/theme_loader.py`

```python
"""Theme discovery and application for arc-tui.

Built-in themes live in src/ui/themes/*.tcss.
User themes live in ~/.arc/themes/*.tcss.
User themes take precedence if they share a name with a built-in.

Usage:
    loader = ThemeLoader()
    print(loader.available())        # list of theme names
    loader.apply(app, "dracula")     # reload app CSS to dracula theme
"""
from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ui.app import ArcApp

_USER_THEME_DIR = Path.home() / ".arc" / "themes"
_BUILTIN_NAMES = [
    "default", "dracula", "nord", "tokyo-night",
    "gruvbox-dark", "solarized-dark", "light",
]


class ThemeLoader:
    """Discovers and applies TCSS themes."""

    def __init__(self) -> None:
        # Path to the bundled themes directory (src/ui/themes/).
        # __file__ is src/ui/theme_loader.py; themes/ is a sibling directory.
        self._builtin_dir = Path(__file__).parent / "themes"

    def available(self) -> list[str]:
        """Return all available theme names (built-in + user), sorted."""
        names: set[str] = set(_BUILTIN_NAMES)
        if _USER_THEME_DIR.exists():
            for f in _USER_THEME_DIR.glob("*.tcss"):
                names.add(f.stem)
        return sorted(names)

    def tcss_path(self, name: str) -> Path | None:
        """Return the Path to a theme's .tcss file, or None if not found.

        User themes take precedence over built-ins of the same name.
        """
        user_path = _USER_THEME_DIR / f"{name}.tcss"
        if user_path.exists():
            return user_path
        builtin_path = self._builtin_dir / f"{name}.tcss"
        if builtin_path.exists():
            return builtin_path
        return None

    def apply(self, app: "ArcApp", name: str) -> bool:
        """Apply a theme to the running Textual app by reloading CSS.

        Returns True on success, False if the theme name is not found.

        Textual 8.x provides App.stylesheet which can be reloaded.
        The approach here: update App.CSS_PATH to point to the new theme
        file and call app.refresh_css() if available, else app._reload_css().

        NOTE: Textual 8.x CSS reloading API — confirm exact method name:
        - Check `dir(app)` for `refresh_css`, `reload_css`, or `_reload_css`.
        - In Textual 0.86+, `app.set_reactive(App.CSS_PATH, [...])` then
          `app.refresh_css()` is the documented path.
        - If no public API exists, write the combined TCSS (vars + theme + app CSS)
          to a temp file and reload from there.
        Implement using whichever method is available in the installed version.
        """
        path = self.tcss_path(name)
        if path is None:
            return False

        # Load the vars contract + this theme's overrides as one combined TCSS.
        vars_path = self._builtin_dir / "_vars.tcss"
        combined = ""
        if vars_path.exists():
            combined += vars_path.read_text() + "\n"
        combined += path.read_text()

        # Store the current theme name on the app for settings persistence.
        app._active_theme = name

        # Apply via Textual's CSS reload mechanism.
        # The implementer must confirm the exact API for Textual 8.2.5.
        # Option A: app.set_class (if Textual exposes per-class CSS vars — it doesn't)
        # Option B: write combined to a temp file, update app.CSS_PATH
        # Option C: use app.stylesheet.add_css(combined) if available
        # Use whichever works; document the choice in a comment.
        try:
            # Textual 8.x approach: update DEFAULT_CSS on the app class and refresh.
            # This is a documented dynamic CSS update pattern.
            type(app).DEFAULT_CSS = combined
            # trigger a full CSS refresh — method varies by version
            if hasattr(app, "refresh_css"):
                app.refresh_css()
            elif hasattr(app, "_reload_css"):
                app._reload_css()
        except Exception as exc:
            # Log but don't crash if the reload API has changed.
            import logging
            logging.getLogger(__name__).warning(f"theme reload failed: {exc}")
            return False

        return True


# Module-level singleton.
_loader: ThemeLoader | None = None


def get_theme_loader() -> ThemeLoader:
    global _loader
    if _loader is None:
        _loader = ThemeLoader()
    return _loader
```

### Wire into `ArcApp`

In `src/ui/app.py`, add theme loading at startup and expose `_theme_names`:

```python
from ui.theme_loader import get_theme_loader

class ArcApp(App):
    ...
    def on_mount(self) -> None:
        loader = get_theme_loader()
        self._theme_names = loader.available()
        # Apply the theme from settings (Phase 0083j); default for now.
        active = getattr(self, "_active_theme", "default")
        loader.apply(self, active)
        ...
```

### Wire `/theme` command in `builtin.py`

Replace the stub `_cmd_theme` from Phase 0083h:

```python
async def _cmd_theme(app: "ArcApp", args: str) -> None:
    from ui.theme_loader import get_theme_loader
    loader = get_theme_loader()
    parts = args.strip().split()
    if not parts:
        app.notify("Themes: " + ", ".join(loader.available()), timeout=5)
        return
    name = parts[0]
    if name == "generate":
        app.notify("Theme generator coming in Phase 0083l")
        return
    ok = loader.apply(app, name)
    if ok:
        app.notify(f"Theme: {name}")
    else:
        app.notify(f"Unknown theme: {name}", severity="error")
```

## Verification

```bash
# 1. All theme files are syntactically valid TCSS
# (Textual validates on load — any syntax error will crash on apply)

# 2. Theme discovery returns expected names
python - <<'EOF'
from ui.theme_loader import ThemeLoader
loader = ThemeLoader()
names = loader.available()
expected = {"default", "dracula", "nord", "tokyo-night", "gruvbox-dark", "solarized-dark", "light"}
missing = expected - set(names)
assert not missing, f"Missing themes: {missing}"
print(f"Found themes: {names}")
EOF

# 3. Manual: launch arc-tui, type /theme dracula → entire UI repaints with Dracula colors
# 4. Manual: place a custom .tcss in ~/.arc/themes/mytheme.tcss → appears in /theme list
# 5. Existing tests pass
pytest -x -q
```

## Done when

- [ ] `themes/_vars.tcss` and all 7 theme `.tcss` files created.
- [ ] Every widget file references only CSS variables (`$bg`, `$primary`, etc.) — no literal colors.
- [ ] `ThemeLoader.available()` returns all 7 built-in names plus any user themes.
- [ ] `ThemeLoader.apply(app, name)` reloads the CSS in the running app.
- [ ] `/theme list` (no args) prints available themes via `app.notify()`.
- [ ] `/theme dracula` switches the theme; the TUI visually repaints.
- [ ] User theme in `~/.arc/themes/*.tcss` appears in the list.
- [ ] `pytest` green.

## Out of scope for this phase

- Settings persistence of the active theme (Phase 0083j).
- Theme generator UI (Phase 0083l).
- Light mode detection / automatic theme based on terminal background.
