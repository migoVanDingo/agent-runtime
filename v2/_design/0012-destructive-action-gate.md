# 0012 — destructive-action gate

## Motivation

Today the `guard` plugin handles two kinds of policy:

- `blocklist_patterns` — hard deny via `ToolDenial` (e.g., `rm -rf`, `mkfs`).
- `escalation_required_patterns` — prompt the user; auto-deny in headless
  mode (e.g., `curl`, `sudo`).

There's a gap in the middle: ordinary `rm <file>`, `git reset --hard`,
`git push --force`, redirects to existing files, `truncate`, etc.  These
are destructive but not categorically forbidden — exactly the cases where
the agent might do the right thing 99% of the time and the wrong thing the
1% that ruins your day.

Review of session `SES01KS6F96XN330BP25H6HNNQTTV` (see conversation log of
2026-05-22): the user asked the agent to clean up a directory.  The agent
issued five separate `rm <file>` calls and they all ran without ever
surfacing to the user.  The `rm -rf` retry was correctly denied by the
`guard` blocklist — but single-file `rm`s were not in any category.

This phase adds a dedicated **safety_gate** plugin that pattern-matches a
broader catalog of destructive operations and prompts the user via the
existing `UserGate` before they execute.

---

## Scope

In:
- New plugin `arc.plugins.safety_gate`
- Pattern catalog seeded with defaults; user-extensible via config
- Per-session in-process "remember" cache: once the user approves a
  specific pattern, don't re-prompt for the same pattern again this session
- New event types in the canonical taxonomy: `safety.confirmation.requested`,
  `safety.confirmation.allowed`, `safety.confirmation.denied`
- Log-writer formatters for the new events
- Default plugin entry in `defaults.py` — on by default
- Headless behavior: auto-deny (matches `guard`'s convention)

Out (deferred to a follow-up):
- Structured destruction preview ("would delete N files totaling M bytes") —
  needs filesystem inspection, more code than fits in this phase
- Persistent approvals across sessions — would need a state file under
  `$ARC_HOME/safety/`
- Pattern-specific UI hints ("y/N/always") in the TUI gate — current
  implementation reuses the existing `prompt_for_escalation` (yes/no only)

---

## Hook integration

Single hook: `before_tool_call`.

Order: safety_gate fires **after** guard (priority 20 vs guard's 10) so
guard's hard denies short-circuit first and we don't bother the user about
commands that were going to be denied anyway.  The decision tree at a tool
call:

```
1. guard.before_tool_call (priority 10)
     - allowlist tool → pass
     - blocklist match → ToolDenial (hard)
     - escalation match → ask user → pass | ToolDenial
2. safety_gate.before_tool_call (priority 20)
     - check call.input["command"] against patterns
     - if remembered-approved this session → pass
     - else → ask user → pass + remember | ToolDenial
3. tool executes
```

The two plugins are deliberately independent.  Guard owns the "what is
flat-out banned" axis; safety_gate owns the "what is destructive enough to
double-check" axis.

---

## Pattern catalog (defaults)

Each pattern is an object with `name`, `description`, `regex`.  The name
keys the remember-cache.  The description is shown in the prompt.

| Name | What it catches | Default regex |
|---|---|---|
| `rm-file` | `rm <file>` (single file) | `^\s*rm\s+(?!-)` |
| `rm-recursive` | `rm -r`, `rm -R` (non `-rf` already caught by guard) | `\brm\s+-r\b` |
| `git-reset-hard` | `git reset --hard` | `\bgit\s+reset\s+--hard\b` |
| `git-clean-force` | `git clean -fd`, `git clean -f` | `\bgit\s+clean\s+-(\w*f\w*)\b` |
| `git-push-force` | `git push --force`, `git push -f`, `--force-with-lease` | `\bgit\s+push\s+(?:--force(?:-with-lease)?\|-f)\b` |
| `truncate` | `truncate -s` | `\btruncate\b\s+-s` |
| `chown-recursive` | `chown -R` | `\bchown\s+-R\b` |
| `chmod-recursive` | `chmod -R` | `\bchmod\s+-R\b` |
| `redirect-overwrite` | `> existing_file` (single `>` not `>>`) | `(?<![>])>\s*[^\s>&]` |
| `drop-table` | SQL `DROP TABLE` | `(?i)\bdrop\s+table\b` |
| `drop-database` | SQL `DROP DATABASE` | `(?i)\bdrop\s+database\b` |
| `truncate-sql` | SQL `TRUNCATE` | `(?i)\btruncate\s+(table\s+)?\w+` |

`redirect-overwrite` is intentionally noisy — it fires on any `>` that isn't
`>>`.  Acceptable cost for catching `cmd > important_file`.  Users who find
it annoying can remove it from `enabled_patterns`.

Patterns are applied to `call.input["command"]` only (same as guard).  Tools
that don't have a `command` field aren't checked.

---

## UserGate flow

Reuse the existing `UserGate.prompt_for_escalation(EscalationRequest)
-> bool` contract:

- `TUIGate` prompts interactively and returns the user's decision.
- `NoOpGate` (headless `arc run`) always denies.

On approval, the pattern name is added to a per-session in-process
`_approved_this_session: set[str]`.  Subsequent matches against the same
pattern pass through without prompting.

No persistence across sessions in v1.  Adding it later is a config key
+ a small JSON file under `$ARC_HOME/safety/approvals.json`.

---

## New events

```
EventType.SAFETY_CONFIRMATION_REQUESTED  "safety.confirmation.requested"
EventType.SAFETY_CONFIRMATION_ALLOWED    "safety.confirmation.allowed"
EventType.SAFETY_CONFIRMATION_DENIED     "safety.confirmation.denied"
```

Payloads:

```json
// requested
{ "tool_name": "bash_exec",
  "command": "rm _tests/file.enc",
  "pattern_name": "rm-file",
  "remembered": false }

// allowed
{ "tool_name": "bash_exec",
  "command": "rm _tests/file.enc",
  "pattern_name": "rm-file",
  "scope": "once" }   // "once" | "session" | "remembered"

// denied
{ "tool_name": "bash_exec",
  "command": "rm _tests/file.enc",
  "pattern_name": "rm-file" }
```

`scope: "remembered"` means the user previously approved this pattern this
session and the plugin auto-passed without re-prompting.  We still emit
the event so the audit trail captures every destructive action that ran.

Log-writer formatters render these as:
```
⚠ safety: rm-file → asking user
✓ safety: rm-file → allowed (scope=session)
✖ safety: rm-file → denied
```

---

## Config keys

Under `plugins.enabled[]`:

```yaml
- name: safety-gate
  config:
    enabled: true                  # master switch; false = pass-through
    bypass_mode: false             # CI/test convenience — never prompts, always passes
    enabled_patterns:              # which catalog patterns are active
      - rm-file
      - rm-recursive
      - git-reset-hard
      - git-clean-force
      - git-push-force
      - truncate
      - chown-recursive
      - chmod-recursive
      - redirect-overwrite
      - drop-table
      - drop-database
      - truncate-sql
    custom_patterns: []            # user-defined: [{name, description, regex}, ...]
  hooks_order:
    before_tool_call: 20
```

- `bypass_mode` exists so the test suite can disable the gate without
  setting `enabled: false` (which removes the plugin entirely from event
  logs — useful to *test* the plugin while exercising the rest of the
  system).
- `custom_patterns` lets users add their own without forking the plugin.
  Same shape as the catalog: `{name: str, description: str, regex: str}`.

---

## Headless mode

Same convention as `guard`: `NoOpGate` always denies, with a stderr line:

```
[safety] denied (no interactive user): bash_exec — rm-file
```

`arc run "delete _tests/x"` will therefore stop at the destructive call.
This is intentional — headless arc should never silently destroy.  Users
who want to script destructive operations can either:
- Add the relevant patterns to `safety_gate.bypass_mode: true` for that
  run via a config override
- Or move the patterns to `guard.allowlist_tools` if they want broader bypass

---

## File layout

```
src/arc/plugins/safety_gate/
  __init__.py
  plugin.py                  ← main class
  catalog.py                 ← default patterns
tests/unit/test_safety_gate.py
```

Plus:
- `src/arc/runtime/events.py` — three new EventType constants
- `src/arc/plugins/__init__.py` — `_build_safety_gate` + `_BUILDERS` entry
- `src/arc/plugins/log_writer/formatter.py` — three new formatters
- `src/arc/defaults.py` — new `safety-gate` entry under `plugins.enabled`

---

## Test plan

Unit tests cover:
1. Pattern matching (each catalog entry against representative commands).
2. Allowed pass-through (no pattern → no prompt).
3. Allowed via user approval (gate returns True → tool runs).
4. Denied via user denial (gate returns False → ToolDenial returned).
5. Remember-cache (same pattern matched twice → only one prompt).
6. Bypass mode (gate is never invoked).
7. Headless (NoOpGate denies all → ToolDenial every time).
8. Custom patterns merge with catalog correctly.
9. Events emitted on each path (requested + allowed | denied).
10. Tool calls without a `command` field are pass-through.

Smoke: enable the plugin, run `arc run "delete a file"` (it'll be denied by
NoOpGate, as expected), then `arc` interactive and confirm the prompt
appears.

---

## State on disk

None.  Approvals are in-process for the session and disappear on exit.
Cross-session persistence is a follow-up.

---

## Why this is its own plugin (and not just more guard patterns)

The two plugins have different mental models:

- `guard`: "this command is in a banned category" — a *policy* layer.
  Patterns are categorical: "this kind of thing is never OK / sometimes
  OK with approval."
- `safety_gate`: "this command is destructive and you should think about
  it" — a *human-in-the-loop* layer.  Patterns are about reversibility,
  not categorical permissibility.

Keeping them separate lets users:
- Disable guard entirely (for CI environments where you trust the agent)
  while keeping safety_gate (because even trusted CI shouldn't silently
  `git push --force`).
- Disable safety_gate entirely (for fully autonomous batch runs where
  approval prompts deadlock the run) while keeping guard.
- Reason about each layer's decisions independently in the event log.

---

## Implementation notes

Shipped as one pass. Code layout matches the design exactly:

- [`src/arc/plugins/safety_gate/plugin.py`](../src/arc/plugins/safety_gate/plugin.py)
  — the class, ~135 lines including comments.  Implements `before_tool_call`
  only.  Bus is wired post-construction via `bind_bus` (same pattern as
  `sliding_window_context`).
- [`src/arc/plugins/safety_gate/catalog.py`](../src/arc/plugins/safety_gate/catalog.py)
  — 12 default patterns as a `tuple[Pattern, ...]`.  Frozen dataclass.
- [`src/arc/runtime/events.py`](../src/arc/runtime/events.py) §
  `SAFETY_CONFIRMATION_*` — three new constants.
- [`src/arc/plugins/log_writer/formatter.py`](../src/arc/plugins/log_writer/formatter.py)
  — three new `_fmt_safety_*` functions + dispatch entries.  Logger name
  is `arc.safety` so users can filter independently.
- [`src/arc/plugins/__init__.py`](../src/arc/plugins/__init__.py)
  `_build_safety_gate` — constructs the plugin, applies the `bus`, parses
  custom patterns from the config dict.
- [`src/arc/defaults.py`](../src/arc/defaults.py) — new entry under
  `plugins.enabled` between `guard` and `pause-resume` (preserving the
  intended ordering).

### Test coverage (`tests/unit/test_safety_gate.py`, 24 tests)

| Area | Tests |
|---|---|
| Pattern matching | rm-file, rm-recursive vs rm -rf, git reset --hard, git push --force (3 forms), redirect overwrite vs append, drop-table case insensitive |
| Pass-through | safe commands, non-command tools, `enabled=false`, `bypass_mode=true` |
| Decision flow | user approves → pass, user denies → ToolDenial with model guidance |
| Remember cache | second occurrence of same pattern doesn't re-prompt; per-pattern not global |
| Events | requested + allowed (scope=session), requested + denied, remembered → allowed (scope=remembered) |
| Headless | NoOpGate denies all destructive calls |
| Custom patterns | merge with catalog, unknown catalog names silently ignored |
| Catalog sanity | every default pattern is valid regex |

### Operational caveats

- **Existing user configs need a manual update** to enable the new plugin.
  The config loader doesn't auto-add new `plugins.enabled` entries — that
  would silently change behavior on existing installs.  Users either:
  - Run `arc bootstrap --force` (overwrites their config with defaults), or
  - Manually paste the `safety-gate` entry from the default into their
    `config.yml`.
  We could close this by making the plugin loader fall back to "build any
  registered plugin not in config.yml using its defaults," but that's a
  bigger philosophical change about config strictness — out of scope for
  this phase.

- **NoOpGate's stderr line says `[guard]`** rather than `[safety]` — it's a
  fixed prefix in `user_gate.py`.  Cosmetic only; the events still record
  the right plugin.  Worth fixing alongside the next user_gate touchup.

- **Headless `arc run` will refuse destructive ops by default.**  This is
  deliberate.  For batch jobs that need to run destructively, set
  `plugins.enabled[name=safety-gate].config.bypass_mode: true` in a
  scratch config and pass it via `--home <path>`.

### Deferred (per design § Scope > Out)

- Structured destruction preview (filesystem dry-run before approval)
- Persistent approvals across sessions
- TUI gate "allow always" affordance (current TUIGate is yes/no only)

## State

Shipped.  386 unit tests pass (24 new for safety_gate).  Live smoke
confirms registration, priority ordering vs guard (20 > 10), headless
denial flow, and event emission.
