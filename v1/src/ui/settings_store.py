"""User settings — Pydantic model + YAML-backed store.

Settings file: ~/.arc/settings.yml
Created with defaults on first launch if absent.

The SettingsStore is a singleton; import get_settings_store() everywhere.
Changes are validated by Pydantic before writing to disk.

Exported: Settings, SettingsStore, get_settings_store
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, Field, ValidationError

_log = logging.getLogger(__name__)
_SETTINGS_PATH = Path.home() / ".arc" / "settings.yml"


class Settings(BaseModel):
    """User-level arc-tui settings.

    All settings have defaults so a missing or empty settings.yml is valid.
    Adding new fields with defaults ensures forward-compat: old settings files
    simply use the default for any new key (via extra = "ignore").
    """

    # Appearance
    theme: str = "default"

    # Editor / input
    submit_key: str = "ctrl+enter"   # "ctrl+enter" | "enter"
    history_size: int = Field(default=100, ge=1, le=10_000)

    # Display
    status_bar_visible: bool = True
    show_elapsed_timer: bool = True

    # Scrollback
    scrollback_lines: int = Field(default=5_000, ge=100, le=100_000)

    model_config = {"extra": "ignore"}  # forward-compat: ignore unknown keys


class SettingsStore:
    """Loads, validates, and persists user settings.

    Change listeners are called synchronously after each successful save.
    Register with add_change_listener(callback) where callback receives
    (key: str, value: Any) for each changed setting.
    """

    def __init__(self, path: Path = _SETTINGS_PATH) -> None:
        self._path = path
        self._settings: Settings = Settings()
        self._listeners: list[Callable[[str, Any], None]] = []
        self._load()

    def _load(self) -> None:
        """Load settings from YAML; fall back to defaults on any error."""
        if not self._path.exists():
            return
        try:
            raw = yaml.safe_load(self._path.read_text()) or {}
            if not isinstance(raw, dict):
                raw = {}
            self._settings = Settings(**raw)
        except (ValidationError, yaml.YAMLError, OSError) as exc:
            _log.warning("settings load failed (%s); using defaults", exc)
            self._settings = Settings()

    def _save(self) -> None:
        """Persist current settings to YAML, creating parent dirs as needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = self._settings.model_dump()
        self._path.write_text(yaml.dump(data, default_flow_style=False))

    # ── Read / write ──────────────────────────────────────────────────────────

    @property
    def settings(self) -> Settings:
        """The current in-memory Settings object (read-only reference)."""
        return self._settings

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value by key, with an optional default."""
        return getattr(self._settings, key, default)

    def set(self, key: str, value: Any) -> None:
        """Validate and update a single setting. Persists immediately.

        Raises ValueError if the key does not exist or the value is invalid.
        """
        if not hasattr(self._settings, key):
            raise ValueError(
                f"Unknown setting: {key!r}. Valid keys: {self.known_keys()}"
            )
        # Validate via a full model copy — Pydantic will coerce and validate.
        try:
            updated = self._settings.model_copy(update={key: value})
            # Re-validate by round-tripping through model_validate.
            Settings.model_validate(updated.model_dump())
        except (ValidationError, TypeError) as exc:
            raise ValueError(f"Invalid value for {key!r}: {exc}") from exc

        setattr(self._settings, key, value)
        self._save()
        # Notify listeners after persisting.
        for listener in list(self._listeners):
            try:
                listener(key, value)
            except Exception:
                pass

    # ── Listeners ─────────────────────────────────────────────────────────────

    def add_change_listener(self, cb: Callable[[str, Any], None]) -> None:
        """Register a callback invoked after each successful set()."""
        if cb not in self._listeners:
            self._listeners.append(cb)

    def remove_change_listener(self, cb: Callable[[str, Any], None]) -> None:
        """Unregister a previously added listener."""
        try:
            self._listeners.remove(cb)
        except ValueError:
            pass

    # ── Convenience ───────────────────────────────────────────────────────────

    def known_keys(self) -> list[str]:
        """Return all valid setting key names."""
        return list(Settings.model_fields.keys())


# Module-level singleton — one store per process.
_store: SettingsStore | None = None


def get_settings_store() -> SettingsStore:
    """Return the module-level SettingsStore singleton."""
    global _store
    if _store is None:
        _store = SettingsStore()
    return _store
