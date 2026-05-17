.PHONY: help install install-prod dev test test-fast lint format bootstrap run clean clean-sessions

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip

help:  ## list available targets
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## editable install (development)
	$(PIP) install -e .

install-prod:  ## non-editable install
	$(PIP) install .

dev:  ## install + dev extras (pytest, ruff)
	$(PIP) install -e ".[dev]"

test:  ## run all tests
	$(PYTHON) -m pytest

test-fast:  ## stop on first failure, quiet
	$(PYTHON) -m pytest -x -q

lint:  ## ruff check
	$(PYTHON) -m ruff check src tests

format:  ## ruff format
	$(PYTHON) -m ruff format src tests

bootstrap:  ## create ARC_HOME and default config
	arc bootstrap

run:  ## start an interactive session
	arc

clean:  ## remove __pycache__ and .pyc
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete

clean-sessions:  ## DELETE ALL SESSIONS (confirms first)
	@read -p "This deletes EVERY session in ARC_HOME. Type 'yes' to confirm: " ans; \
	if [ "$$ans" = "yes" ]; then arc sessions --rm-all-confirmed; else echo "aborted"; fi

.DEFAULT_GOAL := help
