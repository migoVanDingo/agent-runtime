"""`~/.arc/catalog.yml` loader for the `arc setup` picker.

The catalog file is user-editable YAML — they own the list of pickable
models per provider.  This module reads it, validates the shape, and
returns typed entries.  Malformed files fall back to the shipped default
plus a one-line warning (per design 0017's failure-mode table).

See _design/0017-provider-picker.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # PyYAML — read-only, no comment-preservation needed here

from arc.defaults import DEFAULT_CATALOG_YAML

log = logging.getLogger("arc.setup.catalog")


# Reserved sentinel id used at runtime to mean "let the user type a model id."
# Always appended to every list returned by `load_catalog`.
MANUAL_ENTRY_ID = "__manual__"
MANUAL_ENTRY_LABEL = "type a model id manually…"


class CatalogError(ValueError):
    """`catalog.yml` is malformed in a way that prevents using the user's file.

    The user-facing message names the offending entry index and the
    missing/invalid field so they can fix it.
    """


@dataclass(frozen=True)
class CatalogEntry:
    """One pickable model in the catalog menu.

    `id` is what gets written into `provider.model` on selection.
    `label` is the display string in the picker.
    `note` is an optional trailing hint, shown dim-colored.
    """
    id: str
    label: str
    note: str = ""


# ── Public API ─────────────────────────────────────────────────────────────


def load_catalog(catalog_path: Path) -> dict[str, list[CatalogEntry]]:
    """Read catalog.yml; return {provider_name: [CatalogEntry, ...]}.

    Behavior:
      - File missing: returns shipped default, logs a warning.
      - File malformed (parse error / wrong shape): returns shipped default,
        logs a warning naming the file + the problem.
      - Per-provider key missing in the user file: filled from shipped default.
      - Per-entry missing required field: raises CatalogError (the user
        needs to fix it; we can't silently drop one entry from a curated list).

    The manual-entry sentinel is appended to every list at return time —
    callers never have to worry about adding it themselves.
    """
    user_data = _try_load(catalog_path)
    default_data = yaml.safe_load(DEFAULT_CATALOG_YAML) or {}

    if user_data is None:
        # Fall back to defaults entirely; user-facing files-missing is
        # rare-but-recoverable since arc bootstrap creates the file.
        return _finalize(default_data)

    # Merge: per-provider keys present in user_data override default_data.
    # Anything missing from user_data is filled from default_data so a
    # half-edited catalog still produces sensible menus.
    merged: dict[str, Any] = dict(default_data)
    if isinstance(user_data, dict):
        for key, value in user_data.items():
            merged[key] = value
    else:
        log.warning(
            "catalog at %s isn't a YAML mapping at the top level; using shipped default",
            catalog_path,
        )
        return _finalize(default_data)

    return _finalize(merged)


def append_manual_sentinel(entries: list[CatalogEntry]) -> list[CatalogEntry]:
    """Return entries + the manual-entry sentinel at the end.

    Picker code calls this just before rendering so the sentinel is
    always available, even if the user's file is empty.  Idempotent —
    if the sentinel is already present, returns the list unchanged.
    """
    if any(e.id == MANUAL_ENTRY_ID for e in entries):
        return entries
    return list(entries) + [CatalogEntry(id=MANUAL_ENTRY_ID, label=MANUAL_ENTRY_LABEL)]


# ── Internals ──────────────────────────────────────────────────────────────


def _try_load(path: Path) -> Any:
    """Return parsed YAML or None on missing/malformed file.

    None is the "use shipped defaults" signal.  Real parse errors are
    surfaced as warnings — we don't want a corrupted catalog.yml to
    break `arc setup` entirely.
    """
    if not path.exists():
        log.warning("catalog at %s missing; using shipped default", path)
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        log.warning("catalog at %s is not valid YAML (%s); using shipped default", path, e)
        return None


def _finalize(data: dict[str, Any]) -> dict[str, list[CatalogEntry]]:
    """Validate and normalize a (possibly user-merged) catalog dict.

    Each provider key maps to a list of CatalogEntry.  Required fields
    on each entry are `id` and `label`; missing required fields raise
    CatalogError naming the offender.
    """
    out: dict[str, list[CatalogEntry]] = {}
    for provider, entries_raw in data.items():
        if not isinstance(provider, str):
            raise CatalogError(
                f"catalog: provider key must be a string, got {type(provider).__name__}"
            )
        if entries_raw is None:
            out[provider] = []
            continue
        if not isinstance(entries_raw, list):
            raise CatalogError(
                f"catalog: provider {provider!r} must be a list of entries, "
                f"got {type(entries_raw).__name__}"
            )

        parsed: list[CatalogEntry] = []
        for i, raw in enumerate(entries_raw):
            if not isinstance(raw, dict):
                raise CatalogError(
                    f"catalog: {provider}[{i}] must be a mapping with id/label/note, "
                    f"got {type(raw).__name__}"
                )
            try:
                entry_id = raw["id"]
                entry_label = raw["label"]
            except KeyError as e:
                raise CatalogError(
                    f"catalog: {provider}[{i}] missing required field {e.args[0]!r}"
                ) from None
            if not isinstance(entry_id, str) or not entry_id.strip():
                raise CatalogError(
                    f"catalog: {provider}[{i}].id must be a non-empty string"
                )
            if not isinstance(entry_label, str) or not entry_label.strip():
                raise CatalogError(
                    f"catalog: {provider}[{i}].label must be a non-empty string"
                )
            note = raw.get("note", "") or ""
            parsed.append(CatalogEntry(
                id=str(entry_id),
                label=str(entry_label),
                note=str(note),
            ))
        out[provider] = parsed
    return out
