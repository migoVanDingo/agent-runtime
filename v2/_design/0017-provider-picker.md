# 0017 — Provider picker (`arc setup`)

## Motivation

After 0014 (Ollama) and 0015 (llama.cpp) land, arc supports four providers
across two very different deployment shapes:

- **Mac / dev box** — Anthropic or Gemini.  Cloud, paid, no GPU needed.
- **Ubuntu inference host** — Ollama or llama.cpp.  Local, free, GPU-backed.

The same git checkout runs in both places.  Today the only way to switch
providers is to hand-edit `~/.arc/config.yml` (or `./.arc/config.yml`).
That's fine the first time on a new host; it's annoying on the inference
host where the user is more likely to swap models, and it's an unforced
error waiting to happen — typo a model id and you don't find out until
the first turn fails.

This phase ships a small interactive command that does three things:

1. Picks a provider from the known list.
2. Picks a model — *for local providers, the list is fetched live from
   the running inference server* so you can only choose models that are
   actually pulled / loaded.
3. Writes the choice back into `config.yml` non-destructively (comments,
   ordering, and unrelated keys preserved).

It is **not** a general config editor.  Provider + model only.  Everything
else (retry, params, tools, plugins) stays the YAML-editing experience.

---

## Scope

In:
- New CLI subcommand `arc setup` — interactive provider+model picker
- Curated model catalogs for cloud providers (Anthropic, Gemini)
- Live model discovery for local providers (Ollama `/api/tags`,
  llama.cpp `/v1/models`)
- Comment-preserving YAML write-back to the resolved `config.yml`
- Sensible defaults for `base_url` and `api_key_env` when switching to a
  provider that needs them
- "Type your own" escape hatch for any provider (for models not in our
  curated list, or experimental ollama tags)
- Idempotent — re-running just updates the two keys; no backup files
- Auto-runs `arc bootstrap` first if no `config.yml` exists yet

Out (deferred):
- Editing anything other than `provider.name` and `provider.model` (plus
  the two derived keys `base_url` and `api_key_env`).  No temperature,
  max_tokens, plugin toggles, etc.  Those stay in the YAML.
- A `/provider` mid-session slash command for swapping providers
  without restarting.  Tempting but invalidates in-flight context;
  defer until we have a real reason.
- API-key entry / validation.  Keys live in env vars; the picker doesn't
  touch them.  It *does* warn if `os.environ[api_key_env]` is unset.
- A "test this provider" round-trip after selection.  Adds a guaranteed-slow
  step to setup; users can just run `arc run "hi"` themselves.
- Cost/latency hints in the menu ("Opus 4.7 — $$$, slow").  Cute but
  the pricing table already shows real numbers in the TUI toolbar.

---

## Architecture

```
src/arc/
  setup/
    __init__.py             ← re-exports run_setup()
    picker.py               ← prompt_toolkit menus, the user-facing flow
    catalog.py              ← catalog.yml loader (NOT a hardcoded registry)
    discovery.py            ← live fetchers for ollama/llama_cpp
    writer.py               ← comment-preserving YAML mutation
  defaults.py               ← +DEFAULT_CATALOG_YAML alongside DEFAULT_CONFIG_YAML
  cli.py                    ← +`setup` subcommand wiring
tests/unit/test_setup_catalog.py
tests/unit/test_setup_writer.py
tests/unit/test_setup_discovery.py
tests/integration/test_setup_picker.py   ← scripted prompt_toolkit input
```

Single-purpose module.  Doesn't touch the runtime, doesn't import from
`arc.runtime.*`, doesn't subscribe to events.  It's a config-file editor
with a curated knowledge base.

### CLI surface

```
arc setup                    interactive picker, writes ~/.arc/config.yml
arc setup --home <path>      same, but target a specific ARC_HOME
arc setup --print            run picker, print the resulting YAML to stdout,
                             don't write (useful for dry-runs / piping)
arc setup --provider <name>  skip provider menu, jump to model menu
arc setup --provider <name> --model <id>
                             fully non-interactive; just write and exit
                             (script-friendly, idempotent)
```

`arc setup` with no args is the everyday case.  The flag forms exist so
scripted setup (CI, Ansible, dotfile sync) doesn't need to drive a TTY.

### Picker flow

```
$ arc setup
arc setup — pick a provider and model

  Provider:
    ( ) anthropic      Cloud, paid.  Best for long-context reverse-engineering.
    ( ) gemini         Cloud, paid.  Fast, generous free tier.
    (•) ollama         Local, free.  Requires `ollama serve` running.
    ( ) llama_cpp      Local, free.  Requires `llama-server` running.

  [enter to continue, q to abort]

→ Provider: ollama
  Querying http://localhost:11434/api/tags …
  Found 4 pulled models.

  Model:
    (•) llama3.1:8b                 (4.7 GB, tools ✓)
    ( ) llama3.2:3b                 (2.0 GB, tools ✗ — no tool support)
    ( ) hermes3:8b                  (4.7 GB, tools ✓)
    ( ) qwen2.5:14b                 (8.2 GB, tools ✓)
    ( ) type a model id manually…

  [enter to continue, b to go back]

→ Model: llama3.1:8b
  Writing to /home/migo/.arc/config.yml …
  Updated: provider.name = ollama
  Updated: provider.model = llama3.1:8b
  Updated: provider.base_url = http://localhost:11434/v1
  Updated: provider.api_key_env = OLLAMA_API_KEY  (env var not set; ollama doesn't validate, fine)

Done.  Run `arc` to start a session.
```

Implementation: prompt_toolkit's `radiolist_dialog` covers it.  ~30 lines
per menu, 80-ish total for the flow.

### Model catalog (cloud) — YAML-driven

The cloud-provider model list lives in `~/.arc/catalog.yml` (or
`./.arc/catalog.yml` per ARC_HOME resolution).  `catalog.py` is a loader,
not a registry — it reads the file, validates the shape, and falls back
to a shipped default if the file is missing or malformed.

User benefits:
- Add a new model the day it launches without waiting for an arc release.
- Curate per-machine — show only models the user has API access to.
- Reorder so the user's preferred default sits at the top of the list.
- Other tooling (CI, dotfile sync, custom plugins) can rewrite the file.

`~/.arc/catalog.yml` shape:

```yaml
# arc model catalog — drives the `arc setup` picker.
# Add, remove, reorder freely.  Picker shows entries top-to-bottom.

anthropic:
  - id: claude-opus-4-7
    label: "Opus 4.7"
    note: "most capable, slowest"
  - id: claude-sonnet-4-6
    label: "Sonnet 4.6"
    note: "balanced; default recommendation"
  - id: claude-haiku-4-5
    label: "Haiku 4.5"
    note: "fastest, cheapest"

gemini:
  - id: gemini-2.5-pro
    label: "Pro"
    note: "most capable"
  - id: gemini-2.5-flash
    label: "Flash"
    note: "balanced default"
  - id: gemini-3.1-flash-lite-preview
    label: "Flash-Lite"
    note: "cheapest"

# Local-provider entries are discovered live (Ollama /api/tags,
# llama.cpp /v1/models). Leave empty unless you want to pin a default.
ollama: []
llama_cpp: []
```

Schema (one entry per model):

| Field | Required | Notes |
|---|---|---|
| `id` | yes | The string written into `provider.model` on selection |
| `label` | yes | Display text in the picker menu |
| `note` | no | Trailing hint shown dim-colored in the picker |

Loader contract (`catalog.py`):

```python
@dataclass(frozen=True)
class CatalogEntry:
    id: str
    label: str
    note: str = ""

def load_catalog(home: Path) -> dict[str, list[CatalogEntry]]:
    """Read ~/.arc/catalog.yml; merge with shipped defaults for any
    provider key absent from the user file.  Raises CatalogError with
    a clear message on malformed YAML / missing required fields."""
```

The shipped default (`DEFAULT_CATALOG_YAML` in `defaults.py`, mirroring
`DEFAULT_CONFIG_YAML`) is what gets written to disk on first `arc setup`
run.  Same lifecycle as `config.yml`: created once, never overwritten.

Always append a `CatalogEntry(id="__manual__", label="type a model id manually…")`
sentinel to every list at runtime.  Selecting it opens a text-input
dialog and writes whatever the user types (non-empty + no whitespace
check; arc startup will surface real errors).

### Model discovery (local)

#### Ollama
```python
def fetch_ollama_models(base_url: str) -> list[OllamaModel]:
    resp = httpx.get(f"{base_url.rstrip('/v1')}/api/tags", timeout=5)
    resp.raise_for_status()
    return [
        OllamaModel(
            name=m["name"],
            size_gb=m["size"] / 1e9,
            has_tools=_capabilities_for(m["name"]).tool_use,
        )
        for m in resp.json().get("models", [])
    ]
```

Reuses `_capabilities_for` from 0014 to flag tool support in the menu.

Failure → graceful: catch `httpx.HTTPError`, fall back to "couldn't
reach Ollama at <url> — start it with `ollama serve`, or type a model
id manually."  The manual entry path still works.

#### llama.cpp
```python
def fetch_llama_cpp_models(base_url: str) -> list[LlamaCppModel]:
    resp = httpx.get(f"{base_url.rstrip('/v1')}/v1/models", timeout=5)
    resp.raise_for_status()
    return [LlamaCppModel(id=m["id"]) for m in resp.json().get("data", [])]
```

`llama-server` usually has exactly one model loaded.  Menu shows just
that one, plus the manual entry sentinel.  If the user wants to switch
the loaded model, they need to restart `llama-server` with a different
`-m` flag — out of scope for arc.

Failure → same graceful fallback as Ollama.

### Defaults the picker writes alongside the choice

| Provider | `base_url` | `api_key_env` |
|---|---|---|
| anthropic | `null` | `ANTHROPIC_API_KEY` |
| gemini | `null` | `GEMINI_API_KEY` |
| ollama | `http://localhost:11434/v1` | `OLLAMA_API_KEY` |
| llama_cpp | `http://localhost:8080/v1` | `LLAMA_CPP_API_KEY` |

If the user's existing config has a non-null `base_url` for the selected
provider already (e.g. they're pointing at a custom port), **preserve it**.
The picker should only set these keys when they're currently `null` or
absent, never override a user-set value.

### YAML write-back

PyYAML can't preserve comments on round-trip.  Two options:

**Option A — add `ruamel.yaml` as a dep.**  Round-trip mode preserves
comments, key order, blank lines, the lot.  Used widely; well-maintained;
~MB-scale dep with no transitive bloat.

**Option B — surgical line edit.**  Read file as text, regex/seek to
`provider:` block, rewrite the four target lines (name, model, base_url,
api_key_env), leave everything else untouched.  Brittle if the user
reformatted the block.

Recommendation: **Option A**.  The brittleness of option B doesn't pay
for itself; ruamel.yaml is a standard dep for "config file you want to
edit programmatically."  Single new line in `pyproject.toml`.

Implementation outline:

```python
from ruamel.yaml import YAML

def write_provider_choice(
    config_path: Path,
    *,
    name: str,
    model: str,
    base_url: str | None,
    api_key_env: str,
) -> list[str]:
    yaml = YAML()
    yaml.preserve_quotes = True
    with config_path.open() as f:
        data = yaml.load(f)

    changes: list[str] = []
    prov = data["provider"]

    def maybe_set(key: str, value, *, only_if_missing_or_none: bool = False):
        existing = prov.get(key)
        if only_if_missing_or_none and existing not in (None, ""):
            return
        if existing != value:
            prov[key] = value
            changes.append(f"provider.{key} = {value!r}")

    maybe_set("name", name)
    maybe_set("model", model)
    maybe_set("base_url", base_url, only_if_missing_or_none=True)
    maybe_set("api_key_env", api_key_env, only_if_missing_or_none=True)

    with config_path.open("w") as f:
        yaml.dump(data, f)
    return changes
```

Tests pin the round-trip: original file with comments + manual choice
→ written file diff is exactly the four (or fewer) lines, nothing else.

---

## Bootstrap interaction

`arc bootstrap` (and the auto-bootstrap inside `arc setup`) now writes
**two** files when missing:

1. `~/.arc/config.yml` from `DEFAULT_CONFIG_YAML` — unchanged from today.
2. `~/.arc/catalog.yml` from `DEFAULT_CATALOG_YAML` — new.

Both are gitignored, both are written once and never overwritten unless
`--force` is passed.  The bootstrap result reports `wrote_config` and
`wrote_catalog` separately.

So a fresh clone on the Ubuntu remote is:

```
$ git clone … && cd v2 && pip install -e .
$ arc setup
  (creates .arc/, writes catalog.yml + config.yml, walks picker)
$ arc run "hi"
```

No manual YAML editing at any point — but when the user *wants* to edit
(add a new model, reorder), it's a clearly-shaped file in a known
location.

---

## Failure modes

| Failure | Behavior |
|---|---|
| Local server unreachable when fetching model list | Show clear error, fall back to manual entry, do not crash. |
| `ruamel.yaml` parse fails on existing config | Surface the parse error verbatim with the line number.  Don't overwrite a broken file. |
| `catalog.yml` malformed or missing | Fall back to the shipped default catalog and log a one-line warning naming the file + the problem.  Picker still works. |
| `catalog.yml` has zero entries for the selected provider | Show only the `__manual__` sentinel.  User can still proceed by typing an id. |
| User aborts (q / Ctrl+C) | No writes.  Existing config untouched. |
| `--provider` flag with unknown provider name | Exit 2 with a clear list of known providers. |
| `--model` flag without `--provider` | Exit 2; provider must be specified. |
| User picks a provider that requires an env var, and it's not set | Warn (don't fail).  Tell them exactly which env var to export.  arc startup will give the real error later if they actually try to use it. |
| `arc setup --print` | Runs the picker, dumps the resulting YAML to stdout, exits.  Useful for previewing or piping into a different `ARC_HOME`. |

---

## Observability

Nothing.  `arc setup` is a config editor; it doesn't participate in the
event system, doesn't write to any session log, doesn't appear in
`arc sessions`.  The diff it printed in the terminal is the only record.

---

## File layout

```
src/arc/setup/__init__.py
src/arc/setup/picker.py
src/arc/setup/catalog.py
src/arc/setup/discovery.py
src/arc/setup/writer.py
src/arc/cli.py                       ← +`setup` subcommand
pyproject.toml                       ← +ruamel.yaml dep
tests/unit/test_setup_catalog.py
tests/unit/test_setup_writer.py
tests/unit/test_setup_discovery.py
tests/integration/test_setup_picker.py
```

---

## Test plan

> Picker tests run anywhere (mocked httpx for discovery, scripted input
> for prompt_toolkit).  No inference server required.

Unit (`test_setup_writer.py`):
1. Round-trip preserves all comments in `DEFAULT_CONFIG_YAML`
2. Only the requested keys change; everything else byte-identical
3. Non-null `base_url` is *not* overwritten when picker has a default
4. Non-null `api_key_env` is *not* overwritten
5. Missing `provider:` block → raises a clear error (don't silently
   create one — config corruption)
6. Returns a list of human-readable change descriptions for the diff
   display

Unit (`test_setup_catalog.py`):
1. Loader parses a valid `catalog.yml` into `dict[str, list[CatalogEntry]]`
2. Missing provider keys in user file fall back to the shipped default
3. Malformed YAML → returns shipped default + emits a warning
4. Missing required field (`id` or `label`) → raises `CatalogError` with
   the offending entry's index
5. Manual sentinel is appended at runtime to every list, regardless of
   what the user wrote
6. Shipped `DEFAULT_CATALOG_YAML` parses and validates cleanly
7. Empty list for a provider → only the manual sentinel surfaces

Unit (`test_setup_discovery.py`):
1. Ollama: parses `/api/tags` payload into `OllamaModel` list
2. Ollama: tool-capability flag matches `_capabilities_for` from 0014
3. Ollama: server unreachable → returns empty list + a reason string,
   does not raise
4. llama.cpp: parses `/v1/models` payload
5. llama.cpp: server unreachable → empty list + reason

Integration (`test_setup_picker.py`):
1. Scripted prompt_toolkit input: select Anthropic → Sonnet 4.6 →
   confirm config.yml has correct keys
2. Scripted input: select Ollama with mocked discovery returning
   [llama3.1:8b, llama3.2:3b] → pick llama3.1:8b → confirm
3. `--provider gemini --model gemini-2.5-flash` → non-interactive,
   writes correct config, exits 0
4. `--provider unknown` → exits 2 with helpful message
5. `--print` → no file changes, YAML on stdout
6. Run on a directory with no `.arc/` → bootstrap fires first,
   then picker

Smoke:
- On Mac: `arc setup`, choose Anthropic → Sonnet 4.6, run `arc run "hi"`,
  confirm it works.
- On Ubuntu (after 0014 lands): `arc setup`, choose Ollama → llama3.1:8b
  from the live list, run `arc run "hi"`, confirm tool call against `ls`
  works end-to-end.

---

## Open questions

1. **Should `arc bootstrap` chain into `arc setup` automatically?**
   Argument for: brand-new users get a guided first run.  Argument
   against: bootstrap is also called by tests and scripts where an
   interactive picker would hang.  Resolution: no — keep them separate.
   `arc setup` calls `arc bootstrap` if needed (one-way dependency).
   Users learn `arc bootstrap && arc setup` as the new-host recipe.

2. **Curate the model list, or fetch from upstream?**  Anthropic and
   Google both publish model lists via API, but those are noisy (every
   snapshot, every preview, deprecated variants).  We curate in
   `~/.arc/catalog.yml` — a YAML file the user owns and edits.  The
   shipped default is a sensible starting point, but the user has full
   control without needing an arc release to add a model.  The
   manual-entry sentinel covers anything not in the file.

3. **Should the picker remember the previous choice as the default
   highlight?**  Yes — read the current `provider.name` and pre-select
   it in the radiolist.  Same for model when re-running with the same
   provider.  ~5 lines.

---

## State

Landed.

---

## Implementation notes

Things that came up during build (none required design changes — just
worth flagging for future-you):

1. **`api_key_env` preservation rule needed a second pass.**  Designed
   as "preserve any non-null user value," but in practice switching
   provider gemini → anthropic via the picker left `api_key_env:
   GEMINI_API_KEY` in the config, which then blew up at runtime when
   the Anthropic provider couldn't find its env var.  Refined the
   writer rule: preserve only when the existing value is a *real
   custom value*; overwrite when it matches one of the standard shipped
   env-var names (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OLLAMA_API_KEY`,
   `LLAMA_CPP_API_KEY`, `OPENAI_API_KEY`).  See
   `_set_overwriting_known_defaults` in `setup/writer.py`.  The
   user-facing diff still shows what changed.

2. **prompt_toolkit dialog patching in tests.**  Initially imported
   `radiolist_dialog` inside the picker functions for lazy loading,
   which broke unit tests using `patch("arc.setup.picker.radiolist_dialog")`.
   Resolved by patching at the source (`prompt_toolkit.shortcuts.
   radiolist_dialog`).  Pattern documented in `test_setup_picker.py`
   for future menus.

3. **`comment-preservation tests` use `yaml.safe_load` for value
   assertions.**  Asserting raw substrings against the round-tripped
   file was brittle because the shipped default has commented-out
   examples (e.g. `# base_url: http://localhost:11434/v1`) that match
   the same substrings as live values.  The reliable check is to
   re-parse the YAML and assert on the parsed dict.

4. **Picker→`arc llm` integration lives in `picker.py`, not in
   `commands.py`.**  When a user picks a `llama_cpp` model that isn't
   already loaded, the picker offers to swap.  The integration is a
   thin call into `arc.llm.commands.start_server` (0018) — no new
   abstraction needed.  The picker tolerates a missing/empty
   `llm_servers.yml` (treats the picked id as not-in-registry and
   prints a hint rather than failing).
