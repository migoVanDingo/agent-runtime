"""Interactive picker flow for `arc setup`.

Walks: provider → model → confirm → write config.yml.  Used in two ways:

  - As a top-level CLI (`arc setup`) — wraps `run_setup(home, ...)`.
  - As a library call from 0018 (`arc llm`) or 0019 (`arc replay menu`)
    if/when those want to reuse the picker for inline provider switching.

Defaults the picker writes alongside the choice (per design 0017's table):
  anthropic   → ANTHROPIC_API_KEY
  gemini      → GEMINI_API_KEY
  ollama      → http://localhost:11434/v1   + OLLAMA_API_KEY
  llama_cpp   → http://localhost:8080/v1    + LLAMA_CPP_API_KEY

Each provider gets a base_url default only for local providers; cloud
providers default `base_url` to null (SDK picks).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from arc.bootstrap import bootstrap, paths_for, resolve_home
from arc.setup.catalog import (
    MANUAL_ENTRY_ID,
    CatalogEntry,
    append_manual_sentinel,
    load_catalog,
)
from arc.setup.discovery import (
    DiscoveredModel,
    fetch_llama_cpp_models,
    fetch_ollama_models,
)
from arc.setup.writer import render_changes, write_provider_choice


# ── Provider defaults table ────────────────────────────────────────────────

_PROVIDER_OPTIONS: list[tuple[str, str]] = [
    ("anthropic", "Cloud, paid.  Best for long-context reverse-engineering."),
    ("gemini", "Cloud, paid.  Fast, generous free tier."),
    ("ollama", "Local, free.  Requires `ollama serve` running."),
    ("llama_cpp", "Local, free.  Requires `llama-server` running."),
]

_PROVIDER_DEFAULTS: dict[str, dict[str, str | None]] = {
    "anthropic": {
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url": None,
    },
    "gemini": {
        "api_key_env": "GEMINI_API_KEY",
        "base_url": None,
    },
    "ollama": {
        "api_key_env": "OLLAMA_API_KEY",
        "base_url": "http://localhost:11434/v1",
    },
    "llama_cpp": {
        "api_key_env": "LLAMA_CPP_API_KEY",
        "base_url": "http://localhost:8080/v1",
    },
}


@dataclass
class SetupResult:
    """What the picker chose, what got written, and where."""
    provider: str
    model: str
    config_path: Path
    diff_text: str
    api_key_warning: str | None = None  # set if api_key_env isn't exported
    llm_action_taken: str | None = None  # "started", "swapped", "kept", "declined", None (n/a)


# ── Public entry points ────────────────────────────────────────────────────


def run_setup(
    *,
    home: Path | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    print_only: bool = False,
) -> SetupResult:
    """Top-level entry point for `arc setup`.

    Auto-bootstraps if needed (writes config.yml + catalog.yml +
    llm_servers.yml if any are missing).  Then:

      - If both --provider and --model are given, non-interactive write.
      - If only --provider, jump to the model menu for that provider.
      - Otherwise, walk the full interactive picker.

    `print_only` runs the picker then dumps the resulting YAML to stdout
    without writing to disk.
    """
    home = home or resolve_home()
    bootstrap(home)
    p = paths_for(home)

    catalog = load_catalog(p.catalog_file)

    # 1. Provider step
    if provider_override is not None:
        if provider_override not in _PROVIDER_DEFAULTS:
            known = ", ".join(_PROVIDER_DEFAULTS.keys())
            raise SystemExit(
                f"unknown provider {provider_override!r}; known: {known}"
            )
        provider = provider_override
    else:
        provider = _pick_provider()

    # 2. Model step
    if model_override is not None:
        model = model_override
    else:
        model = _pick_model(provider, catalog, base_url_hint=_default_base_url(provider))

    if not model:
        raise SystemExit("setup aborted: no model selected")

    defaults = _PROVIDER_DEFAULTS[provider]
    base_url = defaults.get("base_url")
    api_key_env = defaults["api_key_env"]

    # 3. Print-only path: emit the would-be result without writing
    if print_only:
        return _print_only(p.config_file, provider, model, base_url, api_key_env)

    # 4. Write to disk (comment-preserving)
    changes = write_provider_choice(
        p.config_file,
        name=provider,
        model=model,
        base_url=base_url,
        api_key_env=api_key_env or "",
    )
    diff = render_changes(changes)

    # 5. Warn (don't fail) if the env var isn't set
    warning = None
    if api_key_env and not os.environ.get(api_key_env):
        if provider in ("anthropic", "gemini"):
            warning = (
                f"env var {api_key_env!r} is not set in this shell — "
                f"export it before running `arc` (or add it to .env)."
            )
        # local providers: missing env var is fine, both default to a placeholder

    llm_action = None
    if provider == "llama_cpp":
        llm_action = _maybe_swap_llm_server(p, model)

    return SetupResult(
        provider=provider,
        model=model,
        config_path=p.config_file,
        diff_text=diff,
        api_key_warning=warning,
        llm_action_taken=llm_action,
    )


def _maybe_swap_llm_server(paths, model_id: str) -> str | None:
    """If the user picked llama_cpp, offer to start/swap the server.

    Returns one of:
      "kept"     — right model already running
      "started"  — no server was running, started one
      "swapped"  — different model running, swapped to picked one
      "declined" — user declined the offer
      "skipped"  — model not in llm_servers.yml; user can `arc llm start` later
      None       — registry unavailable / non-interactive context
    """
    try:
        from arc.llm.commands import restart_server, start_server, stop_server
        from arc.llm.process import status as _proc_status
        from arc.llm.registry import RegistryError, load_registry
    except ImportError:
        return None

    try:
        reg = load_registry(paths.llm_servers_file)
    except RegistryError:
        return None

    # Resolve the picked model id against the registry. If not present,
    # we can't manage it for them; bail with a hint.
    try:
        reg.find(model_id)
    except RegistryError:
        sys.stderr.write(
            f"\nnote: '{model_id}' isn't in {paths.llm_servers_file}.\n"
            f"      add it there and run `arc llm start {model_id}` to launch.\n"
        )
        return "skipped"

    current = _proc_status(llm_dir=paths.llm_dir)
    if current is not None and current.model_id == model_id:
        sys.stderr.write(f"\nllama-server already running with {model_id} (pid {current.pid}).\n")
        return "kept"

    if current is None:
        if not _ask_yes_no(f"start llama-server with {model_id} now?"):
            return "declined"
        rc = start_server(paths, model_id)
        return "started" if rc == 0 else "declined"
    else:
        prompt = f"stop current ({current.model_id}) and start {model_id}?"
        if not _ask_yes_no(prompt):
            return "declined"
        rc = restart_server(paths, model_id)
        return "swapped" if rc == 0 else "declined"


def _ask_yes_no(question: str) -> bool:
    """Tiny y/n prompt that works in TTY + scripted contexts."""
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"{question} [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("", "y", "yes")


# ── Provider step ──────────────────────────────────────────────────────────


def _pick_provider() -> str:
    """Render a radiolist of providers.  Returns the chosen name."""
    from prompt_toolkit.shortcuts import radiolist_dialog

    values = [(name, f"{name:<11} {note}") for name, note in _PROVIDER_OPTIONS]
    choice = radiolist_dialog(
        title="arc setup — provider",
        text="Pick a provider:",
        values=values,
    ).run()
    if choice is None:
        raise SystemExit("setup aborted")
    return choice


# ── Model step ─────────────────────────────────────────────────────────────


def _pick_model(
    provider: str,
    catalog: dict[str, list[CatalogEntry]],
    *,
    base_url_hint: str | None,
) -> str:
    """Render a radiolist of models for the chosen provider.

    Cloud providers: list comes from catalog.yml only.
    Local providers: live discovery merged in front of catalog.yml entries.
    """
    entries = list(catalog.get(provider, []))

    if provider == "ollama" and base_url_hint:
        discovered = fetch_ollama_models(base_url_hint).models
        entries = _merge_discovered(discovered, entries)
    elif provider == "llama_cpp" and base_url_hint:
        discovered = fetch_llama_cpp_models(base_url_hint).models
        entries = _merge_discovered(discovered, entries)

    entries = append_manual_sentinel(entries)
    return _radiolist_models(provider, entries)


def _merge_discovered(
    discovered: list[DiscoveredModel],
    catalog_entries: list[CatalogEntry],
) -> list[CatalogEntry]:
    """Put discovered models first, then anything in catalog.yml not
    already in the discovered set."""
    out: list[CatalogEntry] = []
    seen: set[str] = set()
    for m in discovered:
        out.append(CatalogEntry(id=m.id, label=m.label, note=m.note))
        seen.add(m.id)
    for c in catalog_entries:
        if c.id not in seen:
            out.append(c)
    return out


def _radiolist_models(provider: str, entries: list[CatalogEntry]) -> str:
    from prompt_toolkit.shortcuts import input_dialog, radiolist_dialog

    values = []
    for e in entries:
        label = f"{e.label}"
        if e.note:
            label = f"{label}  ({e.note})"
        values.append((e.id, label))

    choice = radiolist_dialog(
        title=f"arc setup — model for {provider}",
        text="Pick a model:",
        values=values,
    ).run()
    if choice is None:
        raise SystemExit("setup aborted")

    if choice == MANUAL_ENTRY_ID:
        text = input_dialog(
            title="Type a model id",
            text=f"Type the {provider} model id to use:",
        ).run()
        if not text or not text.strip():
            raise SystemExit("setup aborted: empty model id")
        return text.strip()
    return choice


# ── Helpers ────────────────────────────────────────────────────────────────


def _default_base_url(provider: str) -> str | None:
    return _PROVIDER_DEFAULTS.get(provider, {}).get("base_url")


def _print_only(
    config_path: Path,
    provider: str,
    model: str,
    base_url: str | None,
    api_key_env: str,
) -> SetupResult:
    """Run a dry-run write into an in-memory buffer; emit to stdout.

    Implemented by reading the current file, applying the writer logic
    against a copy via a temporary path, then printing the dump.
    """
    import tempfile

    with tempfile.NamedTemporaryFile("w+", suffix=".yml", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        tmp_path.write_text(config_path.read_text(encoding="utf-8"))
    try:
        changes = write_provider_choice(
            tmp_path, name=provider, model=model,
            base_url=base_url, api_key_env=api_key_env or "",
        )
        sys.stdout.write(tmp_path.read_text(encoding="utf-8"))
        return SetupResult(
            provider=provider, model=model,
            config_path=config_path,
            diff_text=render_changes(changes),
        )
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
