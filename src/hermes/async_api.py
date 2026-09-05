"""Hermes — Project 1 Async API.

Submission / observability surface for the async engine.
Exposes:
  GET  /health                    -> liveness + mode
  POST /async/run                 -> submit a workflow graph (persist + dispatch)
  GET  /async/workflows/{id}      -> workflow status + aggregated results
  GET  /async/tasks               -> recent persisted tasks
  GET  /metrics                   -> Prometheus exposition (if prometheus-client installed)
"""
from __future__ import annotations

import os
import tempfile

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .async_engine.contract import (
    EVENT_CREATED,
    Task,
    TaskStatus,
)
from .async_engine.eventbus import InMemoryEventBus
from .async_engine.metrics import NoopMetrics, PrometheusMetrics
from .async_engine.store import AsyncTaskStore

app = FastAPI(title="Hermes Async API", version="1.0.0")


def _backend_dsn() -> str | None:
    return os.environ.get("HERMES_DATABASE_URL") or None


def _build_runtime():
    import threading

    from .async_engine.backends import InMemoryBus, RabbitMQBus
    from .async_engine.orchestrator import AsyncOrchestrator, advance_forever

    mode = os.environ.get("HERMES_ASYNC_MODE", "memory")
    url = os.environ.get("HERMES_RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F")
    db_path = os.environ.get("HERMES_ASYNC_DB_PATH") or tempfile.mktemp(suffix=".db")
    store = AsyncTaskStore(db_path, dsn=_backend_dsn())
    bus = RabbitMQBus(url) if mode == "rabbitmq" else InMemoryBus()
    events = InMemoryEventBus()
    metrics = PrometheusMetrics() if _has_prometheus() else NoopMetrics()
    orch = AsyncOrchestrator(store, bus, events=events, metrics=metrics)
    orch._metrics = metrics
    orch._eventbus = events
    if mode == "memory":
        # single-process mode: a background advancer dispatches dependency tasks
        threading.Thread(
            target=advance_forever, args=(store, bus),
            kwargs={"interval": 0.05}, daemon=True,
        ).start()
    return orch, store, bus, events


def _has_prometheus() -> bool:
    try:
        import prometheus_client  # noqa: F401
        return True
    except Exception:
        return False


_RUNTIME = None


def _orch():
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = _build_runtime()
    return _RUNTIME


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class GraphNode(BaseModel):
    task_id: str
    task_type: str
    deps: list[str] = Field(default_factory=list)
    payload: dict = Field(default_factory=dict)
    priority: int = 5
    max_attempts: int = 3


class SubmitRequest(BaseModel):
    nodes: list[GraphNode]


@app.get("/health")
def health():
    orch, store, bus, events = _orch()
    return {"status": "ok", "mode": os.environ.get("HERMES_ASYNC_MODE", "memory"),
            "tasks": store.task_counts()}


@app.post("/async/run")
def submit(req: SubmitRequest):
    orch, store, bus, events = _orch()
    if not req.nodes:
        raise HTTPException(status_code=422, detail="empty nodes")
    # validate task types up-front
    bad = [n.task_type for n in req.nodes if n.task_type not in
           ("research", "analyze", "report", "notify")]
    if bad:
        raise HTTPException(status_code=422, detail=f"invalid task types: {bad}")
    wf = orch.create_workflow()
    tasks = []
    for n in req.nodes:
        tasks.append(Task(task_id=n.task_id, workflow_id=wf.id, task_type=n.task_type,
                          priority=n.priority, max_attempts=n.max_attempts,
                          payload=n.payload, status=TaskStatus.QUEUED))
    for t in tasks:
        store.create_task(t)
        events.emit(EVENT_CREATED, task_id=t.task_id, workflow_id=wf.id, task_type=t.task_type)
    # persist DAG edges — the orchestrator advancer dispatches chained tasks
    for n in req.nodes:
        if n.deps:
            store.add_dependencies(n.task_id, n.deps)
    # dispatch the roots (no dependencies)
    roots = [n for n in req.nodes if not n.deps]
    for n in roots:
        orch.dispatch(store.get_task(n.task_id))
    return {"workflow_id": wf.id, "dispatched": [n.task_id for n in roots],
            "total": len(tasks)}


@app.get("/async/workflows/{workflow_id}")
def workflow_state(workflow_id: str):
    orch, store, bus, events = _orch()
    try:
        agg = orch.aggregate(workflow_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found")
    return agg


@app.get("/async/tasks")
def list_tasks(limit: int = 100):
    orch, store, bus, events = _orch()
    return {"tasks": store.list_tasks(limit)}


@app.get("/metrics")
def metrics():
    """Prometheus text exposition of Hermes engine metrics."""
    if not _has_prometheus():
        raise HTTPException(status_code=404, detail="prometheus-client not installed")
    import prometheus_client
    from prometheus_client import REGISTRY

    orch, store, bus, events = _orch()
    # keep queue depth gauge fresh
    try:
        from .async_engine.contract import ROUTING
        depth = sum(bus.queue_depth(ROUTING[tt][2]) for tt in ROUTING)
        orch._metrics.set_gauge("task_queue_depth", float(depth))
    except Exception:
        pass
    return Response(
        content=prometheus_client.generate_latest(REGISTRY).decode("utf-8"),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )