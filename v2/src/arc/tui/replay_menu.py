"""`arc replay` with no args (or `/replay` in the TUI) → interactive menu.

Walks: session → mode → provider (override or keep) → model → optional
batch picks → max-cost → confirm.  Then dispatches to the batch driver
or single-target replay path and prints the comparison view at the end.

See _design/0019-cross-provider-replay.md.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from arc.bootstrap import paths_for
from arc.replay.batch import BatchTarget, run_batch
from arc.replay.compare import render_full_comparison
from arc.replay.override import known_providers
from arc.setup.catalog import append_manual_sentinel, load_catalog


# ── Public entry ──────────────────────────────────────────────────────────


def run_replay_menu(*, home: Path, sessions_dir: Path) -> int:
    """Top-level entry point.  Returns a CLI exit code."""
    sessions = _list_sessions(sessions_dir)
    if not sessions:
        print("no recorded sessions yet — record one with `arc run` or `arc` first.",
              file=sys.stderr)
        return 1

    source = _pick_session(sessions)
    if source is None:
        return 0

    mode = _pick_mode()
    if mode is None:
        return 0

    targets: list[BatchTarget] = []
    max_cost = None

    if mode == "live":
        primary = _pick_provider_and_model(home, source)
        if primary is None:
            return 0
        targets.append(primary)
        extras = _pick_additional_targets(home, exclude=[primary])
        targets.extend(extras)
        max_cost = _pick_max_cost()
    else:
        # deterministic — just replay without override; no targets needed
        targets = []

    if not _confirm(source, targets, mode, max_cost):
        return 0

    return _launch(home, sessions_dir, source["session_id"], mode, targets, max_cost)


# ── Step: session picker ──────────────────────────────────────────────────


def _list_sessions(sessions_dir: Path) -> list[dict]:
    """Read sessions/index.jsonl, return rows newest-first."""
    idx = sessions_dir / "index.jsonl"
    if not idx.is_file():
        return []
    rows: list[dict] = []
    for line in idx.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    rows.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return rows


def _pick_session(sessions: list[dict]) -> dict | None:
    from prompt_toolkit.shortcuts import radiolist_dialog

    from arc.tui.themes import active as _active_theme

    values = []
    for r in sessions[:30]:  # cap to keep the menu legible
        sid = r.get("session_id", "?")
        started = (r.get("started_at") or "")[:19]
        provider = r.get("provider", "?")
        model = r.get("model", "?")
        label = f"{sid[:14]}…  {started}  {provider}/{model}"
        values.append((r, label))

    choice = radiolist_dialog(
        title="arc replay — pick a session",
        text="Pick a recorded session to replay:",
        values=values,
        style=_active_theme().pt_style,
    ).run()
    return choice


# ── Step: mode ────────────────────────────────────────────────────────────


def _pick_mode() -> str | None:
    from prompt_toolkit.shortcuts import radiolist_dialog

    from arc.tui.themes import active as _active_theme

    choice = radiolist_dialog(
        title="arc replay — mode",
        text="How should the replay run?",
        values=[
            ("deterministic", "Deterministic — reuse recorded LLM responses (free, fast, verifies replay)"),
            ("live", "Live LLM — call the model fresh (lets you swap providers and compare)"),
        ],
        style=_active_theme().pt_style,
    ).run()
    return choice


# ── Step: provider + model ────────────────────────────────────────────────


def _pick_provider_and_model(home: Path, source: dict) -> BatchTarget | None:
    """Pick the primary provider/model for a live replay."""
    from prompt_toolkit.shortcuts import radiolist_dialog

    from arc.tui.themes import active as _active_theme

    original = f"{source.get('provider', '?')} / {source.get('model', '?')}"
    values = [(("keep", ""), f"keep original ({original})")]
    for p in known_providers():
        values.append(((p, None), p))

    choice = radiolist_dialog(
        title="arc replay — provider",
        text="Which provider should run this replay?",
        values=values,
        style=_active_theme().pt_style,
    ).run()
    if choice is None:
        return None

    provider, _ = choice
    if provider == "keep":
        return BatchTarget(
            provider=source.get("provider", ""),
            model=source.get("model", ""),
            label="keep original",
        )

    model = _pick_model_for_provider(home, provider)
    if model is None:
        return None
    return BatchTarget(provider=provider, model=model)


def _pick_model_for_provider(home: Path, provider: str) -> str | None:
    """Pick a model id from the catalog (cloud) or live discovery (local)."""
    from prompt_toolkit.shortcuts import input_dialog, radiolist_dialog

    from arc.tui.themes import active as _active_theme

    style = _active_theme().pt_style

    paths = paths_for(home)
    catalog = load_catalog(paths.catalog_file)
    entries = list(catalog.get(provider, []))

    # Live discovery for local providers (best-effort; ignore failures)
    if provider == "ollama":
        from arc.setup.discovery import fetch_ollama_models
        for m in fetch_ollama_models("http://localhost:11434/v1").models:
            entries.append(_entry_for_discovered(m))
    elif provider == "llama_cpp":
        from arc.setup.discovery import fetch_llama_cpp_models
        for m in fetch_llama_cpp_models("http://localhost:8080/v1").models:
            entries.append(_entry_for_discovered(m))

    entries = append_manual_sentinel(entries)
    values = [(e.id, f"{e.label}" + (f"  ({e.note})" if e.note else "")) for e in entries]
    choice = radiolist_dialog(
        title=f"arc replay — model for {provider}",
        text="Pick a model:",
        values=values,
        style=style,
    ).run()
    if choice is None:
        return None
    if choice == "__manual__":
        text = input_dialog(
            title="Type a model id",
            text=f"Type the {provider} model id:",
            style=style,
        ).run()
        return (text or "").strip() or None
    return choice


def _entry_for_discovered(m):
    """Wrap a DiscoveredModel as a CatalogEntry for the unified picker."""
    from arc.setup.catalog import CatalogEntry
    return CatalogEntry(id=m.id, label=m.label, note=m.note)


# ── Step: additional targets (batch mode) ─────────────────────────────────


def _pick_additional_targets(home: Path, *, exclude: list[BatchTarget]) -> list[BatchTarget]:
    """Multi-select dialog for batch mode.  Returns 0 or more extra targets."""
    from prompt_toolkit.shortcuts import checkboxlist_dialog

    paths = paths_for(home)
    catalog = load_catalog(paths.catalog_file)

    # Compose a flat options list from the catalog: "provider:model" → label
    excluded_ids = {f"{t.provider}:{t.model}" for t in exclude}
    values: list[tuple[str, str]] = []
    for provider, entries in catalog.items():
        for e in entries:
            spec = f"{provider}:{e.id}"
            if spec in excluded_ids:
                continue
            values.append((spec, f"{provider} / {e.label}"))

    if not values:
        return []

    from arc.tui.themes import active as _active_theme

    picks = checkboxlist_dialog(
        title="arc replay — add more models (batch mode)",
        text="Optionally pick additional models to run the same replay against (space to toggle):",
        values=values,
        style=_active_theme().pt_style,
    ).run()
    if not picks:
        return []

    out: list[BatchTarget] = []
    for spec in picks:
        provider, _, model = spec.partition(":")
        out.append(BatchTarget(provider=provider, model=model))
    return out


# ── Step: max cost ────────────────────────────────────────────────────────


def _pick_max_cost() -> float | None:
    from prompt_toolkit.shortcuts import input_dialog, radiolist_dialog

    from arc.tui.themes import active as _active_theme

    style = _active_theme().pt_style

    choice = radiolist_dialog(
        title="arc replay — max cost (USD)",
        text="Abort the replay if running cost exceeds this cap:",
        values=[
            (None, "unlimited"),
            (1.0, "$1"),
            (5.0, "$5"),
            (10.0, "$10"),
            ("custom", "custom…"),
        ],
        style=style,
    ).run()
    if choice == "custom":
        text = input_dialog(
            title="Custom max cost",
            text="Enter max cost in USD (e.g. 2.50):",
            style=style,
        ).run()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return choice if isinstance(choice, float) else None


# ── Step: confirm ─────────────────────────────────────────────────────────


def _confirm(source: dict, targets: list[BatchTarget], mode: str,
             max_cost: float | None) -> bool:
    from prompt_toolkit.shortcuts import yes_no_dialog

    target_lines = "\n".join(f"  {t.short()}" for t in targets) or "  (none — deterministic replay)"
    msg = (
        f"Source: {source.get('session_id', '?')}\n"
        f"Mode:   {mode}\n"
        f"Targets:\n{target_lines}\n"
        f"Max cost: {'unlimited' if max_cost is None else f'${max_cost:.2f}'}\n"
        f"\nLaunch?"
    )
    from arc.tui.themes import active as _active_theme

    return bool(yes_no_dialog(title="Confirm", text=msg, style=_active_theme().pt_style).run())


# ── Step: launch ──────────────────────────────────────────────────────────


def _launch(
    home: Path,
    sessions_dir: Path,
    source_id: str,
    mode: str,
    targets: list[BatchTarget],
    max_cost: float | None,
) -> int:
    """Dispatch to the batch driver (or a single-target replay) and emit the
    comparison view at the end."""
    executable = [sys.executable, "-m", "arc.cli"]

    if mode == "deterministic":
        argv = [*executable, "--home", str(home), "replay", source_id]
        return subprocess.run(argv, env=os.environ.copy()).returncode

    # Live mode — at least one target
    if not targets:
        print("no targets to run", file=sys.stderr)
        return 1

    print(f"running {len(targets)} target(s) against {source_id}…")
    results = run_batch(
        source_session_id=source_id,
        targets=targets,
        arc_home=home,
        max_cost_usd=max_cost,
        on_target_start=lambda t: print(f"  → {t.short()}", file=sys.stderr),
        on_target_done=lambda r: print(
            f"    {r.target.short()}: rc={r.return_code} session={r.target_session_id or '—'} "
            f"({r.elapsed_seconds:.1f}s)",
            file=sys.stderr,
        ),
    )
    successes = [r for r in results if r.succeeded]
    print(f"\n{len(successes)}/{len(results)} target(s) succeeded.")

    if successes:
        dirs = [sessions_dir / source_id] + [
            sessions_dir / r.target_session_id for r in successes  # type: ignore[arg-type]
        ]
        from arc.tui.pricing import PricingTable
        table = PricingTable(cache_path=home / "pricing_cache.json")
        print()
        print(render_full_comparison(dirs, pricing_table=table))
    return 0 if all(r.succeeded for r in results) else 1
