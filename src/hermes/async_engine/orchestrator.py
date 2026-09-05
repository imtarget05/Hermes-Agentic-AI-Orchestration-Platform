"""AsyncOrchestrator — validate, create workflow/tasks, build DAG, dispatch.

The orchestrator does NOT execute worker logic. It:
  * validates the request
  * creates a workflow aggregate + canonical Task rows (persisted)
  * builds a DAG with dependency resolution
  * publishes ready tasks to the bus (RabbitMQ in production)
  * tracks status and aggregates results from the store

`run_workflow` is a convenience executor that wires local workers to the same
bus so a full parallel run works without any external broker (used by tests
and the "no-broker" mode).
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from .contract import (
    EVENT_COMPLETED,
    EVENT_CREATED,
    EVENT_FAILED,
    Task,
    TaskStatus,
    Workflow,
    routing_for,
)
from .dag import TaskDAG, build_dag
from .worker import Worker, WorkerPool

DEFAULT_TASK_TYPES = ("research", "analyze", "report", "notify")
VALID_TASK_TYPES = set(DEFAULT_TASK_TYPES)


class AsyncOrchestrator:
    def __init__(self, store, bus, events=None, metrics=None):
        self.store = store
        self.bus = bus
        self.events = events if events is not None else _NoopEvents()
        self.metrics = metrics
        self._dispatch_lock = threading.RLock()
        self._dispatched: set[str] = set()

    def validate(self, payload: dict[str, Any], task_types: list[str]) -> None:
        if not isinstance(task_types, list) or not task_types:
            raise ValueError("task_types must be a non-empty list")
        bad = [t for t in task_types if t not in VALID_TASK_TYPES]
        if bad:
            raise ValueError(f"invalid task type(s): {bad}")

    def create_workflow(self) -> Workflow:
        wf_id = Workflow().id
        return self.store.create_workflow(wf_id)

    def create_tasks(self, graph: list[dict[str, Any]], workflow_id: str) -> list[Task]:
        """Persist canonical Task rows for every graph node (status queued)."""
        tasks: list[Task] = []
        for node in graph:
            task = Task(
                task_id=node.get("task_id") or Task().task_id,
                workflow_id=workflow_id,
                parent_task_id=node.get("parent_task_id"),
                task_type=node["task_type"],
                priority=node.get("priority", 5),
                max_attempts=node.get("max_attempts", 3),
                deadline=node.get("deadline", ""),
                payload=node.get("payload", {}),
                metadata=node.get("metadata", {}),
                status=TaskStatus.QUEUED,
            )
            self.store.create_task(task)
            self.events.emit(EVENT_CREATED, task_id=task.task_id,
                             workflow_id=workflow_id, task_type=task.task_type)
            tasks.append(task)
        return tasks

    # ---- dispatch ----
    def dispatch(self, task: Task) -> None:
        exchange, routing_key, queue = routing_for(task.task_type)
        self.bus.publish(exchange, routing_key, task.to_message())

    def dispatch_all(self, tasks: list[Task]) -> None:
        for t in tasks:
            self.dispatch(t)

    def dispatch_ready(self, dag: TaskDAG) -> list[str]:
        """Publish all currently-ready tasks; returns dispatched task_ids."""
        dispatched = []
        for tid in dag.ready_tasks():
            if tid in self._dispatched:
                continue
            task = self.store.get_task(tid)
            if task.status == TaskStatus.QUEUED:
                self.dispatch(task)
                dispatched.append(tid)
        self._dispatched.update(dispatched)
        return dispatched

    # ---- aggregation ----
    def aggregate(self, workflow_id: str) -> dict[str, Any]:
        tasks = self.store.list_workflow_tasks(workflow_id)
        results = {t.task_id: self.store.task_results(t.task_id) for t in tasks}
        counts = self.store.task_counts()
        completed = all(t.status == TaskStatus.COMPLETED for t in tasks)
        self.store.complete_workflow(workflow_id, "completed" if completed else "failed")
        return {
            "workflow_id": workflow_id,
            "status": self.store.workflow_status(workflow_id),
            "task_count": len(tasks),
            "counts": counts,
            "results": results,
        }

    # ---- end-to-end run (no external broker) ------------------------------ #
    def run_workflow(
        self,
        graph: list[dict[str, Any]],
        handlers: dict[str, Callable[[Task], str]],
        workers: int = 1,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Full parallel DAG run on one bus (InMemory by default). Blocks until
        every task is terminal. Returns the aggregate report."""
        for node in graph:
            if node["task_type"] not in handlers:
                raise ValueError(f"no handler for task_type {node['task_type']}")
        self.validate({}, [n["task_type"] for n in graph])
        wf = self.create_workflow()

        for node in graph:
            if not node.get("task_id"):
                raise ValueError("run_workflow graph nodes require a stable task_id")
        tasks = self.create_tasks(graph, wf.id)
        dag = build_dag([
            {"task_id": t.task_id, "task": t.to_message(),
             "deps": node.get("deps", []) or []}
            for t, node in zip(tasks, graph)
        ])
        self._dispatched = set()

        def handler(task: Task) -> str:
            return handlers[task.task_type](task)

        def on_done(task: Task, result_uri: str) -> None:
            with self._dispatch_lock:
                dag.mark_completed(task.task_id)
                self.events.emit(EVENT_COMPLETED, task_id=task.task_id,
                                 workflow_id=wf.id, worker_id="orchestrator",
                                 result_uri=result_uri[:200])
                for tid in dag.ready_tasks():
                    if tid not in self._dispatched:
                        self._dispatched.add(tid)
                        self.dispatch(self.store.get_task(tid))

        def on_fail(task: Task, err: str) -> None:
            with self._dispatch_lock:
                dag.mark_failed(task.task_id)
                self.events.emit(EVENT_FAILED, task_id=task.task_id,
                                 workflow_id=wf.id, worker_id="orchestrator",
                                 error=err[:300])

        def build(name: str) -> Worker:
            w = Worker(name, list(handlers.keys()), handler,
                       self.store, self.bus, events=self.events, metrics=self.metrics)
            w.on_task_completed = on_done
            w.on_task_failed = on_fail
            return w

        pool = WorkerPool(build, size=max(1, workers))
        pool.start()
        try:
            for tid in dag.ready_tasks():
                self._dispatched.add(tid)
                self.dispatch(self.store.get_task(tid))
            deadline = time.time() + timeout
            while time.time() < deadline:
                with self._dispatch_lock:
                    pending = [tid for tid, st in dag.status.items() if st == "pending"]
                if not pending:
                    break
                time.sleep(0.005)
        finally:
            pool.stop()
        return self.aggregate(wf.id)


class _NoopEvents:
    def emit(self, event_type, **fields):
        return None


# ---- cross-process DAG advancer (Railway/compose orchestrator service) ---- #
def advance_once(store, bus, limit: int = 50) -> list[str]:
    """Dispatch every task whose deps are all completed (single pass).
    Returns the dispatched task_ids. Idempotent: tasks already published are
    marked `queued` in execution_state and never re-published."""
    dispatched = []
    for task in store.dispatchable_tasks(limit):
        exchange, routing_key, _queue = routing_for(task.task_type)
        bus.publish(exchange, routing_key, task.to_message())
        store.mark_dispatched(task.task_id)
        dispatched.append(task.task_id)
    return dispatched


def advance_forever(store, bus, interval: float = 0.5, limit: int = 50) -> None:
    """Long-running orchestrator advancer: dispatch ready tasks + finalize
    workflows whose tasks are all terminal. Never raises."""
    import time as _time

    while True:
        try:
            advance_once(store, bus, limit)
        except Exception as e:  # advancer must survive transient store/bus issues
            print(f"[advancer] dispatch error: {e}", flush=True)
        try:
            store.finalize_workflows()
        except Exception as e:
            print(f"[advancer] finalize error: {e}", flush=True)
        _time.sleep(interval)