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

import hashlib
import threading
import time
from collections.abc import Callable
from typing import Any

from .budgets import (
    BudgetExceededError,
    CostTracker,
    validate_graph_budget,
)
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
from .eventbus import emit_best_effort
from .state_machine import DAGStateMachine
from .worker import Worker, WorkerPool

DEFAULT_TASK_TYPES = ("research", "analyze", "report", "notify",
                        "price", "vendor", "contract", "spec", "analysis", "verification")
VALID_TASK_TYPES = set(DEFAULT_TASK_TYPES)

# Max retries per task type from policy
MAX_RETRIES_POLICY: dict[str, int] = {
    "research": 3,
    "analyze": 3,
    "report": 2,
    "notify": 5,
    "price": 3,
    "vendor": 3,
    "contract": 2,
    "spec": 3,
    "analysis": 3,
    "verification": 2,
}


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
        """Persist canonical Task rows for every graph node (status queued).

        HERMES-01: the graph must fit within hard spawn budgets before any
        row is written — an oversized/recursive plan never reaches the queue.
        """
        validate_graph_budget(graph)
        tasks: list[Task] = []
        for node in graph:
            task_id = node.get("task_id") or Task().task_id
            idempotency_key = self._generate_idempotency_key(workflow_id, task_id, 1)
            task = Task(
                task_id=task_id,
                workflow_id=workflow_id,
                parent_task_id=node.get("parent_task_id"),
                task_type=node["task_type"],
                priority=node.get("priority", 5),
                max_attempts=node.get("max_attempts", MAX_RETRIES_POLICY.get(node["task_type"], 3)),
                deadline=node.get("deadline", ""),
                payload=node.get("payload", {}),
                metadata=node.get("metadata", {}),
                status=TaskStatus.QUEUED,
                idempotency_key=idempotency_key,
            )
            self.store.create_task(task)
            emit_best_effort(self.events, EVENT_CREATED, task_id=task.task_id,
                             workflow_id=workflow_id, task_type=task.task_type)
            tasks.append(task)
        return tasks

    def _generate_idempotency_key(self, workflow_id: str, task_id: str, attempt: int) -> str:
        """Generate idempotency key from workflow_id, task_id, and attempt."""
        data = f"{workflow_id}:{task_id}:{attempt}"
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    # ---- dispatch ----
    def dispatch(self, task: Task) -> None:
        exchange, routing_key, queue = routing_for(task.task_type)
        self.bus.publish(exchange, routing_key, task.to_message())

    def dispatch_all(self, tasks: list[Task]) -> None:
        for t in tasks:
            self.dispatch(t)

    def dispatch_ready(self, dag: TaskDAG) -> list[str]:
        """Publish all currently-ready tasks; returns dispatched task_ids.
        Checks idempotency key before dispatch to prevent duplicates."""
        dispatched = []
        for tid in dag.ready_tasks():
            if tid in self._dispatched:
                continue
            task = self.store.get_task(tid)
            if task.status == TaskStatus.QUEUED:
                # Check idempotency key before dispatch
                if task.idempotency_key:
                    existing = self.store.check_idempotency_key(task.idempotency_key)
                    if existing and existing != task.task_id:
                        # Already processed by another task - skip
                        continue
                    self.store.store_idempotency_key(task.idempotency_key, task.task_id)
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

    # ---- resume workflow ----
    def resume_workflow(self, workflow_id: str, from_task_id: str | None = None) -> dict[str, Any]:
        """Rebuild DAG from persisted state, skip completed, retry failed with backoff, handle PARTIAL."""
        tasks = self.store.get_tasks_for_resume(workflow_id)
        if not tasks:
            return {"workflow_id": workflow_id, "resumed": 0, "message": "No resumable tasks found"}

        # Rebuild DAG from all workflow tasks
        all_tasks = self.store.list_workflow_tasks(workflow_id)
        dag_nodes = []
        for task in all_tasks:
            deps = self.store.get_dependencies(task.task_id)
            dag_nodes.append({"task_id": task.task_id, "task": task.to_message(), "deps": deps})
        dag = build_dag(dag_nodes)

        # Update DAG status from store
        for task in all_tasks:
            status_map = {
                TaskStatus.COMPLETED: "completed",
                TaskStatus.FAILED: "failed",
                TaskStatus.RUNNING: "running",
                TaskStatus.PARTIAL: "partial",
                TaskStatus.BLOCKED: "blocked",
                TaskStatus.RETRYING: "pending",
                TaskStatus.QUEUED: "pending",
                TaskStatus.CREATED: "pending",
            }
            dag.status[task.task_id] = status_map.get(task.status, "pending")

        resumed_count = 0
        for task in tasks:
            if from_task_id and task.task_id != from_task_id:
                continue

            # Check if can resume using state machine
            if not DAGStateMachine.can_resume(task):
                continue

            # Update execution state for resume
            exec_state = task.execution_state.copy()
            exec_state["attempt"] = task.attempt
            exec_state["last_error"] = task.metadata.get("last_error", "")
            exec_state["resumed_at"] = time.time()
            self.store.update_task_execution_state(task.task_id, exec_state)

            # Generate new idempotency key for retry attempt
            new_key = self._generate_idempotency_key(workflow_id, task.task_id, task.attempt)
            task.idempotency_key = new_key
            self.store.update_task_execution_state(task.task_id, {"idempotency_key": new_key})

            # Reset status to QUEUED for retry
            self.store.set_status(task.task_id, TaskStatus.QUEUED)
            self.store.mark_dispatched(task.task_id)
            self.dispatch(task)
            resumed_count += 1

        return {"workflow_id": workflow_id, "resumed": resumed_count}

    # ---- end-to-end run (no external broker) ------------------------------ #
    def run_workflow(
        self,
        graph: list[dict[str, Any]],
        handlers: dict[str, Callable[[Task], str]],
        workers: int = 1,
        timeout: float = 60.0,
        verifier=None,
        breaker=None,
        task_timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Full parallel DAG run on one bus (InMemory by default). Blocks until
        every task is terminal. Returns the aggregate report."""
        for node in graph:
            if node["task_type"] not in handlers:
                raise ValueError(f"no handler for task_type {node['task_type']}")
        self.validate({}, [n["task_type"] for n in graph])
        validate_graph_budget(graph)
        wf = self.create_workflow()
        # HERMES-09: shared cost/time budget for this workflow run
        tracker = CostTracker(workflow_id=wf.id)

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
            # HERMES-09: hard stop when the workflow budget is exhausted —
            # non-retryable (dead-letter + escalation), never an infinite loop.
            stop = tracker.exceeded()
            if stop:
                raise BudgetExceededError(stop)
            return handlers[task.task_type](task)

        def on_done(task: Task, result_uri: str) -> None:
            with self._dispatch_lock:
                dag.mark_completed(task.task_id)
                emit_best_effort(self.events, EVENT_COMPLETED, task_id=task.task_id,
                                 workflow_id=wf.id, worker_id="orchestrator",
                                 result_uri=result_uri[:200])
                # Update execution state for completed task
                self.store.update_task_execution_state(task.task_id, {
                    "attempt": task.attempt,
                    "status": TaskStatus.COMPLETED.value,
                    "completed_at": time.time(),
                })
                for tid in dag.ready_tasks():
                    if tid not in self._dispatched:
                        self._dispatched.add(tid)
                        self.dispatch(self.store.get_task(tid))

        def on_fail(task: Task, err: str) -> None:
            with self._dispatch_lock:
                dag.mark_failed(task.task_id)
                emit_best_effort(self.events, EVENT_FAILED, task_id=task.task_id,
                                 workflow_id=wf.id, worker_id="orchestrator",
                                 error=err[:300])
                # Update execution state for failed task
                self.store.update_task_execution_state(task.task_id, {
                    "attempt": task.attempt,
                    "status": TaskStatus.FAILED.value,
                    "last_error": err[:300],
                    "failed_at": time.time(),
                })
                # HERMES-06: never leave dependents waiting on a failed task —
                # cascade the terminal failure so the DAG ends deterministically
                # instead of deadlocking on a stuck node.
                for dep_id in dag.descendants(task.task_id):
                    if dag.status.get(dep_id) != "pending":
                        continue
                    dag.status[dep_id] = "failed"
                    reason = f"upstream task {task.task_id} failed: {err[:120]}"
                    self.store.mark_failed(dep_id, reason, worker_id="orchestrator")
                    emit_best_effort(self.events, EVENT_FAILED, task_id=dep_id,
                                     workflow_id=wf.id, worker_id="orchestrator",
                                     error=reason[:300])

        def build(name: str) -> Worker:
            w = Worker(name, list(handlers.keys()), handler,
                       self.store, self.bus, events=self.events, metrics=self.metrics,
                       verifier=verifier, breaker=breaker,
                       timeout_seconds=task_timeout_seconds,
                       cost_tracker=tracker)
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
        agg = self.aggregate(wf.id)
        agg["budget"] = tracker.snapshot()  # HERMES-09: observable budget state
        return agg


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
            store.fail_tasks_with_failed_deps()
        except Exception as e:
            print(f"[advancer] stuck-task sweep error: {e}", flush=True)
        try:
            store.finalize_workflows()
        except Exception as e:
            print(f"[advancer] finalize error: {e}", flush=True)
        _time.sleep(interval)
