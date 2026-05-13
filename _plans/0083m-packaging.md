# 0083m — pyproject extras + entry points

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §7.
> Depends on: **0083f** (Textual skeleton — `arc-tui` entry point must exist before wiring).
> Can land at any point after 0083f; does not block later UI phases.

## Goal

Update `pyproject.toml` to:
- Add `[project.optional-dependencies]` with `tui`, `api` (future), and `dev` extras
- Register the `arc-tui` entry point under `[project.scripts]`
- Keep the existing `arc` entry point unchanged
- Update the setuptools package discovery to include `service/` and `ui/`

After this phase, `pip install ".[tui]"` installs Textual; `pip install .` (no
extras) does not. The import guard in `ui/app.py` (Phase 0083f) ensures that
importing the service layer without Textual is safe.

## File to modify

| File | Change |
|------|--------|
| `pyproject.toml` | Add extras, scripts, package discovery |

No Python source files are modified in this phase.

## Current state of `pyproject.toml`

```toml
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "arc"
version = "0.1.0"
requires-python = ">=3.10"

[project.scripts]
arc = "main:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-dir]
"" = "src"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --tb=short"
pythonpath = ["src"]
```

## Target `pyproject.toml`

```toml
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "arc"
version = "0.1.0"
requires-python = ">=3.10"

# Core dependencies: everything needed to run the agent CLI (arc).
# Textual is NOT here — it is optional via [tui].
# Add any packages currently imported by src/ but not yet listed here.
# The actual list must be read from the installed environment; this is a
# representative skeleton — expand with the real deps from the venv.
dependencies = [
    "anthropic",
    "openai",
    "rich",
    "pydantic>=2.0",
    "pyyaml",
    "sqlmodel",
]

[project.optional-dependencies]
# Terminal UI — installs Textual and its development tools.
# The version pin is >= because Textual 8.2.5 is already installed
# (newer than the 0.86 mentioned in the design doc; API is compatible).
tui = [
    "textual>=0.86",
    "textual-dev",
]

# Future FastAPI server (not implemented in this work).
# Listed here so the pyproject structure is ready; deps are placeholder.
api = [
    "fastapi>=0.110",
    "uvicorn[standard]",
    "websockets",
]

# Developer tools.
dev = [
    "pytest",
    "pytest-asyncio",
    "ruff",
    "mypy",
]

[project.scripts]
arc     = "main:main"
arc-tui = "ui.app:run"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-dir]
"" = "src"

# Include theme .tcss files in the wheel so built-in themes ship with the package.
[tool.setuptools.package-data]
"ui" = ["themes/*.tcss"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --tb=short"
pythonpath = ["src"]

# asyncio_mode = "auto" so all async test functions work without explicit
# @pytest.mark.asyncio on each one.
asyncio_mode = "auto"
```

## Notes on the dependency list

The `dependencies` list in the target above is a skeleton. Before landing this
phase, read the actual installed packages that `arc` imports and ensure they
are listed. Key packages to check:

- `anthropic` — always required (provider)
- `openai` — always required (second provider)
- `rich` — always required (used in spinner, tool output)
- `pydantic>=2.0` — required for settings store (Phase 0083j) and existing ORM models
- `pyyaml` — required for settings store (Phase 0083j) and config reading
- `sqlmodel` — required for the ORM/DAL layer (already in the codebase)

Do NOT add `textual` to `dependencies` — it belongs only in `[tui]`.

## `asyncio_mode = "auto"` note

Adding `asyncio_mode = "auto"` to `[tool.pytest.ini_options]` requires
`pytest-asyncio` to be installed. This is in `[dev]` extras. If it causes
issues with existing tests (e.g., some tests unexpectedly become async),
revert to `asyncio_mode = "strict"` and add `@pytest.mark.asyncio` explicitly
to each async test.

## Verification

```bash
# 1. pip install . (no extras) — Textual must NOT be installed
cd /Users/bubz/Developer/agent/runtime/agent-runtime
pip install . --quiet
python -c "import textual" 2>&1 | grep -q "ModuleNotFoundError" && echo "PASS: textual absent" || echo "FAIL: textual was installed"

# Re-install with tui extra for continued development.
pip install ".[tui]" --quiet

# 2. pip install ".[tui]" — Textual IS installed
pip install ".[tui]" --quiet
python -c "import textual; print(f'textual {textual.__version__} installed: ok')"

# 3. arc command still works
arc --help

# 4. arc-tui entry point is registered
arc-tui --help

# 5. Theme .tcss files are included in the installed package
python - <<'EOF'
import importlib.resources, pathlib
# Check that themes/ directory ships with the installed ui package.
try:
    pkg_path = pathlib.Path(__file__).parent / "src" / "ui" / "themes"
    if pkg_path.exists():
        themes = list(pkg_path.glob("*.tcss"))
        print(f"Found {len(themes)} theme files: {[t.stem for t in themes]}")
    else:
        print("themes/ dir not found at expected path")
except Exception as e:
    print(f"Check failed: {e}")
EOF

# 6. pytest still passes (asyncio_mode change does not break existing tests)
pytest -x -q
```

## Done when

- [ ] `pyproject.toml` has `[project.optional-dependencies]` with `tui`, `api`, `dev`.
- [ ] `[project.scripts]` has both `arc = "main:main"` and `arc-tui = "ui.app:run"`.
- [ ] `[tool.setuptools.package-data]` includes `"ui" = ["themes/*.tcss"]`.
- [ ] `pip install .` (no extras) does not install Textual.
- [ ] `pip install ".[tui]"` installs Textual.
- [ ] `arc --help` and `arc-tui --help` both work after a clean reinstall.
- [ ] `pytest -x -q` still green.

## Out of scope for this phase

- Publishing to PyPI.
- Docker/container build instructions.
- Pinning exact versions beyond the minimum constraints listed above.
- Removing `arc` as the default entry point (see Phase 0083n for migration policy).
