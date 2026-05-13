# 0083j — Settings store + settings modal

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §6.
> Depends on: **0083f** (Textual skeleton), **0083i** (theme system for applying saved theme).

## Goal

Implement user-level settings that persist across sessions:
- `~/.arc/settings.yml` — YAML file, validated by Pydantic
- `SettingsStore` — load, validate, persist, and emit change events
- `SettingsScreen` — Textual modal with categorized form
- `/set <key> <value>` wired to the store
- Reactive bindings so changes apply live (e.g., theme change without restart)

## Files to create / modify

| File | Action |
|------|--------|
| `src/ui/settings_store.py` | **Create** — `Settings` Pydantic model + `SettingsStore` |
| `src/ui/screens/settings.py` | **Create** — `SettingsScreen` modal |
| `src/ui/commands/builtin.py` | **Modify** — wire `/set` and `/settings` to real store |
| `src/ui/app.py` | **Modify** — load settings on startup, wire to theme + store ref |

## Detailed implementation

### `src/ui/settings_store.py`

```python
"""User settings — Pydantic model + YAML-backed store.

Settings file: ~/.arc/settings.yml
Created with defaults on first launch if absent.

The SettingsStore is a singleton; import get_settings_store() everywhere.
Changes are validated by Pydantic before writing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, Field, ValidationError

_SETTINGS_PATH = Path.home() / ".arc" / "settings.yml"


class Settings(BaseModel):
    """User-level arc-tui settings.

    All settings have defaults so a missing or empty settings.yml works.
    """

    # Appearance
    theme: str = "default"

    # Editor
    submit_key: str = "ctrl+enter"  # "ctrl+enter" | "enter"
    history_size: int = Field(default=100, ge=1, le=10_000)

    # Status bar
    status_bar_visible: bool = True
    show_elapsed_timer: bool = True

    # Scrollback
    scrollback_lines: int = Field(default=5_000, ge=100, le=100_000)

    class Config:
        extra = "ignore"   # forward-compat: ignore unknown keys from future versions


class SettingsStore:
    """Loads, validates, and persists user settings.

    Change listeners are called synchronously after each successful save.
    Register with add_change_listener(callback) where callback receives
    the key that changed and the new value.
    """

    def __init__(self, path: Path = _SETTINGS_PATH) -> None:
        self._path = path
        self._settings: Settings = Settings()
        self._listeners: list[Callable[[str, Any], None]] = []
        self._load()

    def _load(self) -> None:
        """Load settings from YAML, using defaults for any missing keys."""
        if not self._path.exists():
            return
        try:
            raw = yaml.safe_load(self._path.read_text()) or {}
            self._settings = Settings(**raw)
        except (ValidationError, yaml.YAMLError, OSError) as exc:
            # On any read/validation error, fall back to defaults and warn.
            import logging
            logging.getLogger(__name__).warning(
                f"settings load failed ({exc}); using defaults"
            )
            self._settings = Settings()

    def _save(self) -> None:
        """Persist current settings to YAML."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = self._settings.model_dump()
        self._path.write_text(yaml.dump(data, default_flow_style=False))

    # ── Read / write ──────────────────────────────────────────────────────────

    @property
    def settings(self) -> Settings:
        return self._settings

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self._settings, key, default)

    def set(self, key: str, value: Any) -> None:
        """Validate and update a single setting. Persists immediately.

        Raises ValueError if the key does not exist or the value is invalid.
        """
        if not hasattr(self._settings, key):
            raise ValueError(f"Unknown setting: {key!r}")
        # Validate via a partial model update.
        try:
            updated = self._settings.model_copy(update={key: value})
            # Re-validate the full model.
            Settings(**updated.model_dump())
        except (ValidationError, TypeError) as exc:
            raise ValueError(f"Invalid value for {key!r}: {exc}") from exc

        setattr(self._settings, key, value)
        self._save()
        for listener in self._listeners:
            try:
                listener(key, value)
            except Exception:
                pass

    def add_change_listener(self, cb: Callable[[str, Any], None]) -> None:
        self._listeners.append(cb)

    def remove_change_listener(self, cb: Callable[[str, Any], None]) -> None:
        try:
            self._listeners.remove(cb)
        except ValueError:
            pass

    # ── Convenience ───────────────────────────────────────────────────────────

    def known_keys(self) -> list[str]:
        """Return all valid setting key names."""
        return list(Settings.model_fields.keys())


_store: SettingsStore | None = None


def get_settings_store() -> SettingsStore:
    global _store
    if _store is None:
        _store = SettingsStore()
    return _store
```

### `src/ui/screens/settings.py`

```python
"""SettingsScreen — modal for viewing and editing user settings.

Opened by /settings command or Ctrl+, keybinding.
Left column: categories. Right column: form for selected category.
Changes are applied live and persisted via SettingsStore.
"""
from __future__ import annotations

try:
    from textual.app import ComposeResult
    from textual.screen import ModalScreen
    from textual.widgets import (
        Button, Input, Label, ListView, ListItem,
        Select, Switch, Static,
    )
    from textual.containers import Horizontal, Vertical
    from textual import on
except ImportError as exc:
    raise ImportError("Textual not installed") from exc

from ui.settings_store import get_settings_store, Settings


_CATEGORIES = ["Appearance", "Editor", "Display", "Advanced"]

_CATEGORY_KEYS: dict[str, list[str]] = {
    "Appearance": ["theme"],
    "Editor":     ["submit_key", "history_size"],
    "Display":    ["status_bar_visible", "show_elapsed_timer", "scrollback_lines"],
    "Advanced":   [],
}

_KEY_LABELS: dict[str, str] = {
    "theme":              "Theme",
    "submit_key":         "Submit key",
    "history_size":       "History size",
    "status_bar_visible": "Show status bar",
    "show_elapsed_timer": "Show elapsed timer",
    "scrollback_lines":   "Scrollback lines",
}


class SettingsScreen(ModalScreen):
    """Settings modal. Press Escape or click Close to dismiss."""

    CSS = """
    SettingsScreen {
        align: center middle;
    }
    #settings-dialog {
        width: 70;
        height: 30;
        background: $bg-elevated;
        border: round $primary;
        padding: 1;
    }
    #category-list {
        width: 20;
        height: 100%;
        border-right: solid $border;
    }
    #form-area {
        width: 1fr;
        height: 100%;
        padding: 0 1;
    }
    #close-btn {
        dock: bottom;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self) -> None:
        super().__init__()
        self._store = get_settings_store()
        self._active_category = "Appearance"

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            yield Static("Settings", classes="title")
            with Horizontal():
                with Vertical(id="category-list"):
                    for cat in _CATEGORIES:
                        yield ListItem(Label(cat), id=f"cat-{cat.lower()}")
                with Vertical(id="form-area"):
                    yield Static("", id="form-content")
            yield Button("Close", id="close-btn", variant="primary")

    def on_mount(self) -> None:
        self._render_category("Appearance")

    def _render_category(self, category: str) -> None:
        self._active_category = category
        keys = _CATEGORY_KEYS.get(category, [])
        form = self.query_one("#form-content", Static)

        # Build a simple text representation for now.
        # Full widget form (Input, Switch, Select) can be added here.
        lines = [f"[bold]{category}[/bold]\n"]
        settings = self._store.settings
        for key in keys:
            label = _KEY_LABELS.get(key, key)
            value = getattr(settings, key)
            lines.append(f"  [dim]{label}:[/dim] {value}")
        if not keys:
            lines.append("  [dim](no settings in this category)[/dim]")
        form.update("\n".join(lines))

    @on(ListItem.Selected)
    def on_list_item_selected(self, event: ListItem.Selected) -> None:
        # Extract category name from the widget id.
        item_id = event.item.id or ""
        if item_id.startswith("cat-"):
            cat = item_id[4:].capitalize()
            # Find matching category (case-insensitive prefix match).
            for c in _CATEGORIES:
                if c.lower().startswith(item_id[4:]):
                    self._render_category(c)
                    break

    @on(Button.Pressed, "#close-btn")
    def on_close(self) -> None:
        self.dismiss()

    def action_dismiss(self) -> None:
        self.dismiss()
```

### Wire into `ArcApp`

```python
# In src/ui/app.py

from ui.settings_store import get_settings_store
from ui.theme_loader import get_theme_loader

class ArcApp(App):
    ...

    def on_mount(self) -> None:
        # Load settings and apply saved theme.
        store = get_settings_store()
        loader = get_theme_loader()
        self._theme_names = loader.available()
        self._settings_store = store

        saved_theme = store.get("theme", "default")
        loader.apply(self, saved_theme)

        # Listen for theme changes from /set or settings modal.
        store.add_change_listener(self._on_setting_changed)

        self.push_screen(ChatScreen())
        self._event_task = asyncio.create_task(self._dispatch_events())

    def _on_setting_changed(self, key: str, value) -> None:
        if key == "theme":
            get_theme_loader().apply(self, str(value))
```

### Wire `/set` and `/settings` commands in `builtin.py`

```python
async def _cmd_set(app: "ArcApp", args: str) -> None:
    from ui.settings_store import get_settings_store
    parts = args.strip().split(maxsplit=1)
    if len(parts) < 2:
        store = get_settings_store()
        app.notify(f"Valid keys: {', '.join(store.known_keys())}", timeout=5)
        return
    key, raw_value = parts
    store = get_settings_store()

    # Attempt type coercion based on the field type.
    settings = store.settings
    current = getattr(settings, key, None)
    try:
        if isinstance(current, bool):
            value = raw_value.lower() in ("true", "1", "yes")
        elif isinstance(current, int):
            value = int(raw_value)
        else:
            value = raw_value
        store.set(key, value)
        app.notify(f"Set {key} = {value!r}")
    except ValueError as exc:
        app.notify(str(exc), severity="error")


async def _cmd_settings(app: "ArcApp", args: str) -> None:
    from ui.screens.settings import SettingsScreen
    await app.push_screen(SettingsScreen())
```

Add `Ctrl+,` keybinding in `ArcApp`:

```python
BINDINGS = [
    ("ctrl+comma", "open_settings", "Settings"),
]

async def action_open_settings(self) -> None:
    from ui.screens.settings import SettingsScreen
    await self.push_screen(SettingsScreen())
```

## Verification

```bash
# 1. SettingsStore round-trips correctly
python - <<'EOF'
import tempfile, pathlib
from ui.settings_store import SettingsStore

with tempfile.NamedTemporaryFile(suffix=".yml", delete=False) as f:
    tmp = pathlib.Path(f.name)

store = SettingsStore(path=tmp)
assert store.get("theme") == "default"
store.set("theme", "dracula")
assert store.get("theme") == "dracula"

# Reload from disk
store2 = SettingsStore(path=tmp)
assert store2.get("theme") == "dracula", "Persistence failed"

# Invalid key raises
try:
    store.set("nonexistent_key", "x")
    assert False, "Should have raised"
except ValueError:
    pass

# Invalid value raises
try:
    store.set("history_size", "not_a_number")
    assert False, "Should have raised"
except ValueError:
    pass

tmp.unlink()
print("SettingsStore: all assertions passed.")
EOF

# 2. /set theme dracula via TUI → UI repaints + settings.yml updated
# 3. Restart arc-tui → dracula theme loads automatically
# 4. Ctrl+, opens settings modal; Escape dismisses it
# 5. pytest still green
pytest -x -q
```

## Done when

- [ ] `src/ui/settings_store.py` created: `Settings` Pydantic model, `SettingsStore` with `get/set/save/load`, `get_settings_store()`.
- [ ] `~/.arc/settings.yml` created on first launch (if absent).
- [ ] Settings persist across restarts.
- [ ] `src/ui/screens/settings.py` created: `SettingsScreen` modal with category nav.
- [ ] `/set theme dracula` changes the theme live and persists to `settings.yml`.
- [ ] `/settings` opens the settings modal.
- [ ] Ctrl+, opens the settings modal.
- [ ] Change listener fires on `set()` — theme reloads without manual restart.
- [ ] `pytest` green.

## Out of scope for this phase

- Full widget-based form in the settings modal (Input/Switch/Select per field) — the current version uses Static text. This is a UX improvement but not a correctness concern.
- Settings for keybindings (beyond the submit-key option).
- Syncing project-level `config.yml` settings through this store.
