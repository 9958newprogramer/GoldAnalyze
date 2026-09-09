PYTHON ?= python3

.PHONY: install run worker redis-up redis-down observability-up observability-down stack-up stack-down mcp eval demo-memory demo-resume demo-resume-fast container-config test lint verify release-check

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

observability-up:
	docker compose -f compose.observability.yaml up -d

observability-down:
	docker compose -f compose.observability.yaml down

stack-up:
	docker compose up -d --build --wait

stack-down:
	docker compose down

mcp:
	.venv/bin/aurumlab-mcp

eval:
	.venv/bin/aurumlab-eval

demo-memory:
	.venv/bin/python scripts/demo_memory.py

demo-resume:
	.venv/bin/python scripts/demo_resume.py --output var/resume-evidence.json

demo-resume-fast:
	.venv/bin/python scripts/demo_resume.py --skip-eval --output var/resume-evidence.json

container-config:
	docker compose config --quiet

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

verify: lint test eval

release-check: verify demo-resume
