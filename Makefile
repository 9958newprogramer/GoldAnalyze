PYTHON ?= python3.12

.PHONY: install run worker redis-up redis-down mcp eval demo-memory test lint verify

install:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -e '.[dev]'

run:
	.venv/bin/aurumlab

worker:
	.venv/bin/aurumlab-worker

redis-up:
	docker compose -f compose.redis.yaml up -d --wait

redis-down:
	docker compose -f compose.redis.yaml down

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
