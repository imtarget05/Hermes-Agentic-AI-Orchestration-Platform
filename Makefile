install:
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:
	PYTHONPATH=src .venv/bin/python -m pytest tests -q

demo-fanout:
	PYTHONPATH=src HERMES_DB_PATH=./hermes_tasks.db HERMES_ROUTING_PATH=./routing.json .venv/bin/python -m hermes.gateway --once --text "research python 3.12" --project demo --strategy fanout

demo-pipeline:
	PYTHONPATH=src HERMES_DB_PATH=./hermes_tasks.db .venv/bin/python -m hermes.gateway --once --text "build hello sandbox file" --project demo --strategy pipeline

demo-critic:
	PYTHONPATH=src HERMES_DB_PATH=./hermes_tasks.db .venv/bin/python -m hermes.gateway --once --text "validate agent handoff" --project demo --strategy critic

inbox:
	PYTHONPATH=src HERMES_DB_PATH=./hermes_tasks.db .venv/bin/python -m hermes.inbox_cli --limit 10

api:
	PYTHONPATH=src .venv/bin/uvicorn hermes.dashboard:app --port 8001

verify: test demo-fanout demo-pipeline demo-critic inbox

# ---- Hermes Project 1 — async task queue engine ----
async-test:
	PYTHONPATH=src .venv/bin/python -m pytest tests/test_async_contract.py tests/test_async_retry.py tests/test_async_dag.py tests/test_async_events.py tests/test_async_store.py tests/test_async_worker.py tests/test_async_orchestrator.py tests/test_async_api.py -q

async-ready:
	PYTHONPATH=src .venv/bin/python -m hermes.async_engine.cli ready

async-loadtest:
	PYTHONPATH=src .venv/bin/python -m hermes.async_engine.cli loadtest

compose-up:
	docker compose up -d

compose-down:
	docker compose down

async-api:
	PYTHONPATH=src HERMES_ASYNC_MODE=memory .venv/bin/uvicorn hermes.async_api:app --port 8000
