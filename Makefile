install:
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:
	PYTHONPATH=src .venv/bin/python -m pytest tests -q

demo-procurement:
	PYTHONPATH=src HERMES_DB_PATH=./hermes_tasks.db HERMES_ROUTING_PATH=./routing.json HERMES_HITL_AUTO_APPROVE=true .venv/bin/python -m hermes.gateway --once --text "Công ty cần mua 50 laptop cho team Engineering. Hãy phân tích 3 báo giá" --project demo

procurement-bench:
	PYTHONPATH=src .venv/bin/python -c "from hermes.procurement.pipeline import run_procurement_benchmark; from hermes.runtime import default_demo_quotes; import json; print(json.dumps(run_procurement_benchmark('mua 50 laptop', default_demo_quotes(), handler_delay_ms=400), indent=1))"

approval-bot:
	PYTHONPATH=src .venv/bin/python -m hermes.messaging.approval_bot

inbox:
	PYTHONPATH=src HERMES_DB_PATH=./hermes_tasks.db .venv/bin/python -m hermes.inbox_cli --limit 10

api:
	PYTHONPATH=src .venv/bin/uvicorn hermes.dashboard:app --port 8001

verify: test demo-procurement inbox

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
