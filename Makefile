.PHONY: test lint

PYTHON := .venv/bin/python3

test:
	PYTHONPATH=src $(PYTHON) -m pytest tests/ -q --tb=short

lint:
	$(PYTHON) -m compileall -q src
