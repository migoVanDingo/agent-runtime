SHELL := /bin/bash

# ── Detection ────────────────────────────────────────────────────────────────
OS := $(shell uname -s)
VENV := .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

# Detect package manager on Linux
ifeq ($(OS),Linux)
  PKG_MGR := $(shell command -v apt-get >/dev/null 2>&1 && echo apt-get || \
                    command -v dnf >/dev/null 2>&1 && echo dnf || \
                    command -v pacman >/dev/null 2>&1 && echo pacman || \
                    echo unknown)
endif

.PHONY: help test lint \
        venv install install-all install-python install-system \
        install-radare2 install-r2ghidra install-angr \
        bootstrap migrate \
        check check-system uninstall-r2ghidra \
        os-info

# ── Help (default) ────────────────────────────────────────────────────────────
help:
	@echo "arc — agent runtime"
	@echo ""
	@echo "Setup targets:"
	@echo "  make install              Install Python deps + core system deps (NO angr)"
	@echo "  make install-all          Install everything including angr (may fail to build)"
	@echo "  make install-python       Install Python deps only (auto-runs bootstrap)"
	@echo "  make install-system       Install core system deps (radare2 + r2ghidra)"
	@echo "  make bootstrap            Create ARC_HOME data layout (~/.arc/ by default)"
	@echo "  make migrate              Move legacy project-dir data into ARC_HOME"
	@echo ""
	@echo "Individual system deps:"
	@echo "  make install-radare2      Install radare2 (brew or apt)"
	@echo "  make install-r2ghidra     Install r2ghidra plugin (requires radare2)"
	@echo "  make install-angr         Install angr Python package (heavy)"
	@echo ""
	@echo "Verification:"
	@echo "  make check                Show status of all dependencies"
	@echo "  make check-system         Show status of system dependencies only"
	@echo "  make os-info              Show detected OS and package manager"
	@echo ""
	@echo "Development:"
	@echo "  make test                 Run pytest"
	@echo "  make lint                 Compile-check all source files"
	@echo ""
	@echo "Note: Ghidra binary must be downloaded manually from"
	@echo "      https://github.com/NationalSecurityAgency/ghidra/releases"

os-info:
	@echo "Operating system : $(OS)"
ifeq ($(OS),Linux)
	@echo "Package manager  : $(PKG_MGR)"
endif
ifeq ($(OS),Darwin)
	@echo "Package manager  : Homebrew (assumed)"
endif

# ── Python venv & deps ───────────────────────────────────────────────────────
venv:
	@if [ ! -d "$(VENV)" ]; then \
	  echo "Creating venv at $(VENV)..."; \
	  python3 -m venv $(VENV); \
	fi

install-python: venv
	@echo "Installing Python dependencies into $(VENV)..."
	@$(PIP) install --upgrade pip --quiet
	@$(PIP) install -e ".[tui,dev]"
	@echo "✓ Python deps installed"
	@$(MAKE) --no-print-directory bootstrap

# Create the centralized data layout under ARC_HOME (default ~/.arc/).
# Run after install-python. Idempotent — safe to re-run.
bootstrap:
	@$(VENV)/bin/arc bootstrap

# Migrate legacy project-dir runtime data into ARC_HOME.
migrate:
	@$(VENV)/bin/arc bootstrap --migrate

# ── System deps: radare2 ─────────────────────────────────────────────────────
install-radare2:
	@if command -v r2 >/dev/null 2>&1; then \
	  echo "✓ radare2 already installed: $$(r2 -v | head -1)"; \
	elif [ "$(OS)" = "Darwin" ]; then \
	  if ! command -v brew >/dev/null 2>&1; then \
	    echo "✗ Homebrew not found. Install from https://brew.sh first."; exit 1; \
	  fi; \
	  echo "Installing radare2 via Homebrew..."; \
	  brew install radare2; \
	elif [ "$(OS)" = "Linux" ]; then \
	  if [ "$(PKG_MGR)" = "apt-get" ]; then \
	    echo "Installing radare2 via apt-get (sudo)..."; \
	    sudo apt-get update && sudo apt-get install -y radare2; \
	  elif [ "$(PKG_MGR)" = "dnf" ]; then \
	    echo "Installing radare2 via dnf (sudo)..."; \
	    sudo dnf install -y radare2; \
	  elif [ "$(PKG_MGR)" = "pacman" ]; then \
	    echo "Installing radare2 via pacman (sudo)..."; \
	    sudo pacman -S --noconfirm radare2; \
	  else \
	    echo "✗ Unsupported Linux distro (no apt/dnf/pacman found)."; \
	    echo "  Install radare2 manually: https://github.com/radareorg/radare2"; exit 1; \
	  fi; \
	else \
	  echo "✗ Unsupported OS: $(OS). Install radare2 manually."; exit 1; \
	fi

# ── System deps: r2ghidra plugin ─────────────────────────────────────────────
install-r2ghidra: install-radare2
	@if r2pm -l 2>/dev/null | grep -qw "r2ghidra"; then \
	  echo "✓ r2ghidra plugin already installed"; \
	else \
	  echo "Installing r2ghidra plugin via r2pm..."; \
	  r2pm -U && r2pm -ci r2ghidra; \
	  echo "✓ r2ghidra installed"; \
	  echo "  Verify with: r2 -c 'pdg @ main' /bin/ls 2>&1 | head -5"; \
	fi

uninstall-r2ghidra:
	@r2pm -uci r2ghidra && echo "✓ r2ghidra uninstalled"

# ── System deps: angr (Python, very heavy — opt-in only) ────────────────────
# angr has native deps (unicorn, pyvex, ailment, claripy) that often fail to
# build without specific compiler / system libraries. Not part of `make install`
# — users opt in explicitly via `make install-angr` only when they need
# symbolic execution tools.
install-angr: venv
	@if $(PYTHON) -c "import angr" 2>/dev/null; then \
	  echo "✓ angr already installed in $(VENV)"; \
	else \
	  echo "Installing angr into $(VENV) (this is heavy — may take several minutes)..."; \
	  echo "If this fails, see https://docs.angr.io/en/latest/getting-started/installing.html"; \
	  $(PIP) install angr || { \
	    echo ""; \
	    echo "✗ angr install failed."; \
	    echo "  Common causes:"; \
	    echo "    macOS: install Xcode CLI tools (xcode-select --install) and Homebrew libffi"; \
	    echo "    Linux: install build-essential, python3-dev, libffi-dev"; \
	    echo "  arc agent works without angr — only symbolic execution tools are disabled."; \
	    exit 1; \
	  }; \
	  echo "✓ angr installed"; \
	fi

# ── Aggregate targets ────────────────────────────────────────────────────────
# install-system: only the system deps that build reliably across machines.
# angr is opt-in (`make install-angr`).
install-system: install-radare2 install-r2ghidra
	@echo ""
	@echo "✓ Core system dependencies installed."
	@echo ""
	@echo "Optional (install on demand):"
	@echo "  make install-angr    Symbolic execution tools (heavy native build)"
	@echo "  Ghidra binary        Download manually from"
	@echo "                       https://github.com/NationalSecurityAgency/ghidra/releases"
	@echo "                       then set GHIDRA_HOME in .env"

install: install-python install-system
	@echo ""
	@echo "✓ Full install complete. Run: source $(VENV)/bin/activate"
	@echo ""
	@echo "Run \`make install-angr\` later if you need symbolic execution tools."

# Optional: install EVERYTHING including angr. Will fail if angr can't build.
install-all: install-python install-system install-angr
	@echo ""
	@echo "✓ Complete install (including angr) finished."

# ── Verification ─────────────────────────────────────────────────────────────
check-system:
	@echo "System dependency status:"
	@if command -v r2 >/dev/null 2>&1; then \
	  echo "  ✓ radare2       $$(r2 -v | head -1)"; \
	else \
	  echo "  ✗ radare2       NOT FOUND  (make install-radare2)"; \
	fi
	@if command -v r2pm >/dev/null 2>&1 && r2pm -l 2>/dev/null | grep -qw "r2ghidra"; then \
	  echo "  ✓ r2ghidra      installed"; \
	else \
	  echo "  ✗ r2ghidra      NOT FOUND  (make install-r2ghidra)"; \
	fi
	@if [ -n "$$GHIDRA_HOME" ] && [ -d "$$GHIDRA_HOME" ]; then \
	  echo "  ✓ Ghidra        $$GHIDRA_HOME"; \
	else \
	  echo "  ⚠ Ghidra        GHIDRA_HOME not set (download from github.com/NationalSecurityAgency/ghidra)"; \
	fi
	@if [ -d "$(VENV)" ] && $(PYTHON) -c "import angr" 2>/dev/null; then \
	  echo "  ✓ angr          installed"; \
	else \
	  echo "  ✗ angr          NOT FOUND  (make install-angr)"; \
	fi

check: check-system
	@echo ""
	@echo "Python dependency status:"
	@if [ ! -d "$(VENV)" ]; then \
	  echo "  ✗ venv          NOT FOUND  (make install-python)"; \
	else \
	  echo "  ✓ venv          $(VENV)"; \
	  if $(PYTHON) -c "from agent import Agent" 2>/dev/null; then \
	    echo "  ✓ arc package   importable"; \
	  else \
	    echo "  ✗ arc package   NOT IMPORTABLE  (make install-python)"; \
	  fi; \
	fi

# ── Development ──────────────────────────────────────────────────────────────
test:
	PYTHONPATH=src $(PYTHON) -m pytest tests/ -q --tb=short

lint:
	$(PYTHON) -m compileall -q src
