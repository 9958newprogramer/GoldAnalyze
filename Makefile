PYTHON ?= python3.12

.PHONY: install run mcp eval demo-memory test lint verify

install:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -e '.[dev]'

run:
	.venv/bin/aurumlab

mcp:
	.venv/bin/aurumlab-mcp

eval:
	.venv/bin/aurumlab-eval

demo-memory:
	.venv/bin/python scripts/demo_memory.py

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

verify: lint test eval
