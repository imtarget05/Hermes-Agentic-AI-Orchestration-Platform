"""CLI for the async engine (Hermes Project 1).

Commands:
  hermes-async ready          -> smoke-test the whole stack on an in-memory bus
  hermes-async loadtest       -> run the N × workers matrix, print report
  hermes-async workflow       -> run a small DAG (parallel analyze) end-to-end
"""
from __future__ import annotations

import tempfile


def make_orchestrator(db_path: str = ""):
    from .backends import InMemoryBus
    from .eventbus import JsonlEventBus
    from .metrics import build_metrics
    from .orchestrator import AsyncOrchestrator
    from .store import AsyncTaskStore

    store = AsyncTaskStore(db_path or tempfile.mktemp(suffix=".db"))
    bus = InMemoryBus()
    events = JsonlEventBus("/tmp/hermes-events.jsonl")
    metrics = build_metrics(False)
    return AsyncOrchestrator(store, bus, events=events, metrics=metrics)


def _demo_handlers():
    import time

    def research(t):
        time.sleep(0.02)
        return f"s3://research/{t.task_id}"

    def analyze(t):
        time.sleep(0.02)
        return f"s3://analyze/{t.task_id}"

    def report(t):
        return f"s3://report/{t.task_id}"

    return {"research": research, "analyze": analyze, "report": report}


def cmd_ready(db_path: str = "", workers: int = 4) -> dict:
    import time

    orch = make_orchestrator(db_path)
    graph = [
        {"task_id": "research-1", "task_type": "research", "deps": []},
        {"task_id": "analyze-1", "task_type": "analyze", "deps": ["research-1"]},
        {"task_id": "analyze-2", "task_type": "analyze", "deps": ["research-1"]},
        {"task_id": "report-1", "task_type": "report", "deps": ["analyze-1", "analyze-2"]},
    ]
    t0 = time.time()
    agg = orch.run_workflow(graph, _demo_handlers(), workers=workers)
    agg["elapsed_seconds"] = round(time.time() - t0, 3)
    return agg


def cmd_loadtest(store_dir: str = "/tmp/hermes-lt", sizes=None) -> str:
    from .loadtest import load_test_report, run_load_test

    report = run_load_test(sizes=tuple(sizes) if sizes else (10, 50, 100, 500),
                           store_dir=store_dir, unit_work_seconds=0.03)
    return load_test_report(report)


def cmd_orchestrator():
    """Long-running DAG advancer (Railway `orchestrator` service): polls the
    store, publishes ready tasks to the bus, finalizes terminal workflows."""
    import os

    from .backends import InMemoryBus, RabbitMQBus
    from .orchestrator import advance_forever
    from .store import AsyncTaskStore

    mode = os.environ.get("HERMES_ASYNC_MODE", "rabbitmq")
    dsn = os.environ.get("HERMES_DATABASE_URL") or None
    db_path = os.environ.get("HERMES_ASYNC_DB_PATH") or tempfile.mktemp(suffix=".db")
    store = AsyncTaskStore(db_path, dsn=dsn)
    if mode == "rabbitmq":
        bus = RabbitMQBus(os.environ.get("HERMES_RABBITMQ_URL", ""))
    else:
        bus = InMemoryBus()
    interval = float(os.environ.get("HERMES_ADVANCE_INTERVAL", "0.5"))
    print(f"orchestrator advancer up: mode={mode} interval={interval}s", flush=True)
    advance_forever(store, bus, interval=interval)


def cmd_work():
    """Long-running worker: consume from RabbitMQ + Postgres store, loop forever."""
    import os
    import time

    from .contract import ROUTING
    from .eventbus import KafkaEventBus
    from .metrics import build_metrics
    from .store import AsyncTaskStore
    from .worker import Worker

    mode = os.environ.get("HERMES_ASYNC_MODE", "rabbitmq")
    if mode != "rabbitmq":
        raise SystemExit("worker requires HERMES_ASYNC_MODE=rabbitmq")

    from .backends import RabbitMQBus

    url = os.environ.get("HERMES_RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F")
    dsn = os.environ.get("HERMES_DATABASE_URL") or None
    db_path = os.environ.get("HERMES_ASYNC_DB_PATH") or tempfile.mktemp(suffix=".db")
    store = AsyncTaskStore(db_path, dsn=dsn)
    bus = RabbitMQBus(url)
    events = KafkaEventBus(os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
    metrics = build_metrics(True)

    handlers = _demo_handlers()
    for tt in ROUTING:
        ex, rk, q = ROUTING[tt]
        bus.declare(ex, rk, q)

    # Role-specific workers for Railway layout: HERMES_WORKER_TASK_TYPES="research"
    # (comma-separated). Default: every task type in one process.
    wanted = os.environ.get("HERMES_WORKER_TASK_TYPES", "")
    types = [t.strip() for t in wanted.split(",") if t.strip()] or list(ROUTING)

    workers = []
    for tt in types:
        if tt not in handlers:
            raise SystemExit(f"unknown task type in HERMES_WORKER_TASK_TYPES: {tt}")
        w = Worker(f"w-{tt}", tt, handlers[tt], store, bus, events=events, metrics=metrics)
        workers.append(w)

    print(f"worker up: {len(workers)} handlers, rabbitmq={url}", flush=True)
    while True:
        for w in workers:
            try:
                w.pump_once()
            except Exception as e:
                print(f"[{w.name}] error: {e}", flush=True)
        time.sleep(0.01)


def main(argv=None) -> None:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "loadtest":
        print(cmd_loadtest())
    elif args and args[0] == "work":
        cmd_work()
    elif args and args[0] == "orchestrator":
        cmd_orchestrator()
    elif args and args[0] == "workflow":
        import json
        agg = cmd_ready()
        print(json.dumps(agg, indent=2, default=str))
    else:  # default: ready / smoke
        import json
        agg = cmd_ready()
        print(json.dumps(agg, indent=2, default=str))
        print("\nStack OK — orchestrator + workers + store + events all wired.")


if __name__ == "__main__":
    main()