.PHONY: install web-install db-up db-down migrate seed test-unit test-integration test-recovery eval-offline eval-quality-prepare eval-quality-smoke run-api run-worker run-web

PYTHON ?= .venv/bin/python
UVICORN ?= .venv/bin/uvicorn
PYTEST ?= .venv/bin/pytest

install:
	$(PYTHON) -m pip install -e ".[dev]"

web-install:
	cd apps/web && pnpm install --frozen-lockfile

db-up:
	docker compose --env-file .env -f infra/compose/docker-compose.yml up -d postgres

db-down:
	docker compose --env-file .env -f infra/compose/docker-compose.yml down

migrate:
	$(PYTHON) -m alembic upgrade head

seed:
	$(PYTHON) -m db.seed

test-unit:
	$(PYTEST) tests/unit -q

test-integration:
	$(PYTEST) tests/integration tests/contract -q

test-recovery:
	$(PYTEST) tests/recovery -q

eval-offline:
	$(PYTHON) -m evals.run_offline

eval-quality-prepare:
	$(PYTHON) -m evals.evaluator_quality prepare

eval-quality-smoke:
	$(PYTHON) -m evals.evaluator_quality run --mode fake --allow-draft --limit 5

.PHONY: test-agents eval-agents eval-architecture-plan eval-architecture-smoke eval-grading-plan

eval-architecture-plan:
	$(PYTHON) -m evals.architecture plan --split test --repeats 3

eval-architecture-smoke:
	$(PYTHON) -m evals.architecture run --mode fake --allow-draft --split dev --limit 2

eval-grading-plan:
	$(PYTHON) -m evals.evaluator_quality plan --split test --repeats 3

test-agents:
	$(PYTHON) scripts/validate_agents.py

eval-agents:
	$(PYTHON) -m evals.agent_suite --mode fake

run-api:
	PYTHONPATH=.:packages:apps $(PYTHON) -m scripts.run_api

run-worker:
	PYTHONPATH=.:packages:apps $(PYTHON) -m worker.main

run-web:
	cd apps/web && node node_modules/vite/bin/vite.js
