"""Comment-preserving config.yml mutation for `arc setup`.

PyYAML drops comments on round-trip; we use ruamel.yaml in round-trip
mode so the picker can edit `provider.name`, `provider.model`,
`provider.base_url`, and `provider.api_key_env` without nuking the
extensive commented examples in the shipped default file.

See _design/0017-provider-picker.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path


# api_key_env values shipped with the default catalog.  If the existing
# config has any of these, the picker is free to overwrite — they're not
# user customizations.
_KNOWN_API_KEY_ENVS = frozenset({
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "OLLAMA_API_KEY",
    "LLAMA_CPP_API_KEY",
    "OPENAI_API_KEY",  # future-proofing for when OpenAI provider lands
})


@dataclass(frozen=True)
class WriteChange:
    """One field that the writer mutated (or left alone), reported back
    to the caller so the picker can render a diff to the user."""
    key: str             # e.g. "provider.name"
    old: str | None
    new: str | None
    skipped: bool = False
    skip_reason: str = ""  # populated when skipped=True


def write_provider_choice(
    config_path: Path,
    *,
    name: str,
    model: str,
    base_url: str | None,
    api_key_env: str,
) -> list[WriteChange]:
    """Mutate the `provider:` block of an existing config.yml.

    Rules:
      - `name` and `model` are always set (the whole point of running setup).
      - `base_url` and `api_key_env` are set ONLY if they're currently
        null/empty/missing.  Honoring an explicit non-null value the user
        already set is the right default — they had a reason.

    Returns one WriteChange per field touched (or skipped).  Empty list
    means no diff at all.
    """
    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")            # round-trip mode preserves comments + order
    yaml.preserve_quotes = True
    yaml.width = 4096                # don't reflow long lines

    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    if data is None or "provider" not in data:
        raise ValueError(
            f"config at {config_path} has no `provider:` block; can't safely "
            f"set provider/model.  Run `arc bootstrap --force` to recreate it."
        )

    prov = data["provider"]
    changes: list[WriteChange] = []

    # name and model are always written.
    changes.append(_set_always(prov, "name", name, key_label="provider.name"))
    changes.append(_set_always(prov, "model", model, key_label="provider.model"))

    # base_url preserves existing non-null/non-empty values.
    changes.append(_set_if_missing(
        prov, "base_url", base_url, key_label="provider.base_url",
    ))
    # api_key_env preserves *custom* values but overwrites known-default
    # env-var names from other providers — otherwise switching from gemini
    # → anthropic via the picker leaves you with GEMINI_API_KEY and a
    # config that can't load.
    changes.append(_set_overwriting_known_defaults(
        prov, "api_key_env", api_key_env, key_label="provider.api_key_env",
        known_defaults=_KNOWN_API_KEY_ENVS,
    ))

    # Dump back to disk.  Use StringIO so we only write if dump succeeds.
    buf = StringIO()
    yaml.dump(data, buf)
    config_path.write_text(buf.getvalue(), encoding="utf-8")

    return changes


def render_changes(changes: list[WriteChange]) -> str:
    """Human-readable diff string for the picker's success screen."""
    lines: list[str] = []
    for c in changes:
        if c.skipped:
            lines.append(f"  - {c.key}: kept existing value ({c.old!r})  [{c.skip_reason}]")
            continue
        if c.old == c.new:
            lines.append(f"  - {c.key}: unchanged ({c.new!r})")
        elif c.old is None:
            lines.append(f"  + {c.key} = {c.new!r}")
        else:
            lines.append(f"  ~ {c.key}: {c.old!r} → {c.new!r}")
    return "\n".join(lines) if lines else "  (no changes)"


# ── Field-set helpers ──────────────────────────────────────────────────────


def _set_always(prov, field: str, value, *, key_label: str) -> WriteChange:
    old = prov.get(field)
    old_str = None if old is None else str(old)
    new_str = None if value is None else str(value)
    if old == value:
        return WriteChange(key=key_label, old=old_str, new=new_str)
    prov[field] = value
    return WriteChange(key=key_label, old=old_str, new=new_str)


def _set_overwriting_known_defaults(
    prov, field: str, value, *, key_label: str, known_defaults: frozenset[str],
) -> WriteChange:
    """Set if missing/null/empty OR if the existing value is in known_defaults.

    Preserves user customizations (anything not in the known-defaults set).
    """
    existing = prov.get(field, None)
    old_str = None if existing is None else str(existing)
    new_str = None if value is None else str(value)

    if existing in (None, ""):
        if value not in (None, ""):
            prov[field] = value
        return WriteChange(key=key_label, old=old_str, new=new_str)

    if str(existing) in known_defaults:
        if existing != value:
            prov[field] = value
        return WriteChange(key=key_label, old=old_str, new=new_str)

    # Looks like a real user customization — preserve it.
    return WriteChange(
        key=key_label, old=old_str, new=new_str,
        skipped=True, skip_reason="user value preserved",
    )


def _set_if_missing(prov, field: str, value, *, key_label: str) -> WriteChange:
    """Write `value` to `prov[field]` only if the field is missing, null,
    or an empty string.  Otherwise leave the user's value alone and
    record a 'skipped' change."""
    existing = prov.get(field, None)
    has_real_value = existing not in (None, "")
    old_str = None if existing is None else str(existing)
    new_str = None if value is None else str(value)

    if has_real_value:
        return WriteChange(
            key=key_label,
            old=old_str,
            new=new_str,
            skipped=True,
            skip_reason="user value preserved",
        )

    if value is None or value == "":
        # Nothing to set, and nothing was there — no-op.
        return WriteChange(key=key_label, old=old_str, new=new_str,
                           skipped=True, skip_reason="no default applies")

    prov[field] = value
    return WriteChange(key=key_label, old=old_str, new=new_str)
