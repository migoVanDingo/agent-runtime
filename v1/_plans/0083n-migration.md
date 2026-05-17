# 0083n — Migration & cleanup

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §10 phase 0083n description.
> Depends on: **all prior 0083 phases**.

## Goal

Land the repository conventions, documentation, and cleanup work that makes
the new architecture self-enforcing:

1. Document the import-discipline rule (where applicable — `CLAUDE.md` or inline).
2. Inventory and explicitly mark every `# TODO(0083-cleanup)` comment.
3. Decide and document the `arc` vs `arc-tui` migration policy.
4. Optional: configure `import-linter` to enforce the `ui/ ↛ runtime/` boundary in CI.

No new features. No code deletions (the spinner refactor is explicitly deferred).
Only documentation, conventions, and optional linting.

## Files to create / modify

| File | Action |
|------|--------|
| `CLAUDE.md` | **Create or modify** — import discipline rule + project conventions |
| `pyproject.toml` | **Modify** — add import-linter config (optional) |
| `src/service/inprocess.py` | **Verify** — confirm all TODO comments are present |
| `src/runtime/tool_loop.py` | **Verify** — confirm checkpoint TODO comment |
| `src/runtime/pipeline.py` | **Verify** — confirm checkpoint TODO comment |

## 1. CLAUDE.md — Import discipline rule

Create (or update) `CLAUDE.md` at the repo root. Add a section for the
`0083` import discipline. The rest of `CLAUDE.md` may already exist with
project conventions — append the section without overwriting existing content.

The section to add:

```markdown
## Import discipline rule (0083 — UI/service boundary)

The codebase has a hard architectural boundary:

```
ui/*       ← must NOT import from → runtime/*, agent.py, tools/*
service/*  ← must NOT import from → ui/*
runtime/*  ← must NOT import from → ui/*, service/*
```

Rationale: `service/` and `runtime/` must be installable without Textual.
`runtime/` must not know about any UI framework. Only `ui/` depends on Textual.

**Allowed:**
- `ui/*` → `service/*` (UI talks to the service layer)
- `service/*` → `runtime/*` (InProcessAgentService wraps agent/runtime)
- `runtime/*` → `runtime/*` (internal runtime imports)

**Forbidden:**
- `ui/*` → `runtime/*`, `agent.py`, `tools/*`
- `service/*` → `ui/*`
- `runtime/*` → `ui/*`, `service/*`

**Enforcement:** Run the import check manually during code review:
```bash
python - <<'EOF'
import pathlib, sys
violations = []
for f in pathlib.Path("src/ui").rglob("*.py"):
    src = f.read_text()
    for bad in ["from runtime", "import runtime", "from agent import", "import agent\n",
                "from tools", "import tools"]:
        if bad in src:
            violations.append((str(f), bad))
if violations:
    print("IMPORT VIOLATIONS:", violations)
    sys.exit(1)
else:
    print("Import discipline ok.")
EOF
```

Optional: configure `import-linter` (see pyproject.toml `[tool.importlinter]`)
for automated CI enforcement.

### Deferred cleanup (tracked by TODO comments)

The following items are deliberately deferred and marked with
`# TODO(0083-cleanup)` in the source. Do not remove them without a
dedicated plan:

1. **Spinner refactor** — `NoopSpinner` is injected into `agent.spinner` in
   `InProcessAgentService.__init__`. All stage files that accept `spinner=`
   as a kwarg should eventually drop the parameter; status should come
   exclusively from `Stage*` events. Not done here to avoid a sweeping change
   across all stage files.

2. **Pipeline checkpoint injection** — `PipelineContext._service_checkpoint`
   is a duck-typed field set by the service layer. A more formal injection
   mechanism (e.g., a dedicated context protocol or dependency injection)
   would be cleaner. Deferred.

3. **`CLIUserGate` vs `TUIUserGate`** — the agent currently has both. The
   service layer injects `TUIUserGate`; the legacy CLI path keeps `CLIUserGate`.
   Once `arc` is fully deprecated, `CLIUserGate` can be removed.
```

## 2. Migration policy: `arc` vs `arc-tui`

**Decision (confirmed for this initiative):**

- `arc` remains the default entry point for scripting, CI, and non-TTY environments.
  It is unchanged and tested. Do not deprecate it in this work.
- `arc-tui` is the new default for interactive use.
- Documentation (README) should recommend `arc-tui` for interactive sessions.
- Both coexist indefinitely; `arc` may eventually be removed in a separate plan
  when the TUI has been proven stable in production.

Document this in `CLAUDE.md` and/or README:

```markdown
## CLI entry points

| Command  | Use for |
|----------|---------|
| `arc`    | Scripts, CI, pipes, non-tty, legacy compatibility |
| `arc-tui`| Interactive sessions — full TUI with markdown, themes, slash commands |

Both are installed by `pip install ".[tui]"`. `arc` alone requires no extras.
```

## 3. TODO(0083-cleanup) inventory

After all prior phases land, grep the codebase for all TODO markers from this
initiative and confirm each is present and correctly attributed:

```bash
grep -rn "TODO(0083" src/ | sort
```

Expected occurrences (at minimum):

| File | Comment |
|------|---------|
| `src/service/inprocess.py` | `# TODO(0083-cleanup): remove spinner from stage signatures entirely.` |
| `src/service/inprocess.py` | `# TODO(0083-cleanup): remove spinner kwarg from ToolLoop / stages.` |
| `src/runtime/pipeline.py` | Comment near checkpoint call noting it is injected by the service |
| `src/runtime/tool_loop.py` | Comment near checkpoint call noting it is injected by the service |
| `src/runtime/pipeline_context.py` | `# TODO(0083-cleanup): consider formal injection mechanism for _service_checkpoint` |

If any are missing, add them. If there are unexpected occurrences of
`TODO(0083-cleanup)` not on this list, review them — they may be stale.

## 4. Optional: import-linter configuration

If the team wants CI enforcement of the import boundary, add to `pyproject.toml`:

```toml
[tool.importlinter]
root_packages = ["service", "ui", "runtime"]

[[tool.importlinter.contracts]]
name = "ui must not import from runtime or agent"
type = "forbidden"
source_modules = ["ui"]
forbidden_modules = ["runtime", "agent", "tools"]

[[tool.importlinter.contracts]]
name = "service must not import from ui"
type = "forbidden"
source_modules = ["service"]
forbidden_modules = ["ui"]

[[tool.importlinter.contracts]]
name = "runtime must not import from ui or service"
type = "forbidden"
source_modules = ["runtime"]
forbidden_modules = ["ui", "service"]
```

And add `import-linter` to the `[dev]` optional dependency:

```toml
dev = [
    "pytest",
    "pytest-asyncio",
    "ruff",
    "mypy",
    "import-linter",   # ← ADD
]
```

Run manually with:

```bash
lint-imports
```

**NOTE:** `import-linter` may flag false positives from `TYPE_CHECKING` blocks
or conditional imports (`try/except ImportError`). Configure exclusions as needed.
The linter is optional — the manual grep check above is sufficient for now.

## Verification

```bash
# 1. CLAUDE.md exists and contains the import discipline section
grep -q "Import discipline rule" CLAUDE.md && echo "PASS" || echo "FAIL: missing section"

# 2. All TODO(0083-cleanup) comments are present
count=$(grep -rn "TODO(0083" src/ | wc -l)
echo "Found $count TODO(0083-cleanup) entries (expect >= 4)"
[ "$count" -ge 4 ] && echo "PASS" || echo "FAIL: expected >= 4 entries"

# 3. Import discipline is clean
python - <<'EOF'
import pathlib, sys
violations = []
for f in pathlib.Path("src/ui").rglob("*.py"):
    src = f.read_text()
    for bad in ["from runtime", "import runtime", "from agent import", "import agent\n",
                "from tools", "import tools"]:
        if bad in src:
            violations.append((str(f), bad))
if violations:
    print("VIOLATIONS:", violations)
    sys.exit(1)
print("Import discipline check: PASS")
EOF

# 4. Both entry points exist and work
arc --help >/dev/null && echo "arc: ok" || echo "arc: FAIL"
arc-tui --help >/dev/null && echo "arc-tui: ok" || echo "arc-tui: FAIL"

# 5. Full test suite passes
pytest -x -q

# 6. Optional: import-linter (if installed)
if command -v lint-imports &>/dev/null; then
    lint-imports && echo "import-linter: PASS" || echo "import-linter: FAIL"
else
    echo "import-linter not installed — skipping"
fi
```

## Done when

- [ ] `CLAUDE.md` contains the import discipline rule section.
- [ ] `CLAUDE.md` documents the `arc` vs `arc-tui` migration policy.
- [ ] `grep -rn "TODO(0083" src/` returns at least 4 entries, one per deferred item.
- [ ] Manual import discipline check passes (no `ui/` → `runtime/` imports).
- [ ] Both `arc` and `arc-tui` are registered in `pyproject.toml` and work after `pip install ".[tui]"`.
- [ ] `pytest -x -q` green.
- [ ] (Optional) `lint-imports` passes if `import-linter` is configured.

## Out of scope for this phase

- Spinner removal from stage signatures (explicitly deferred — see TODO list).
- HTTP/WebSocket transport layer (`api/` — future plan).
- Concurrent turns / multi-session UI.
- `arc` deprecation — that is a separate migration plan, not part of 0083.
