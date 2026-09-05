"""DAG execution — parallel branches with dependency resolution (spec §9).

Research
   │
   ├────> Analyze
   │
   └────> Analyze 2
                │
                ▼
              Report

Report is only dispatched once *both* Analyze branches are COMPLETED.
This module resolves which tasks are ready (all deps satisfied), so the
orchestrator never does a naive `for task in tasks: run(task)`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskDAG:
    """Adjacency-list DAG keyed by task_id."""

    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)  # task_id -> task dump
    dependencies: dict[str, set[str]] = field(default_factory=dict)  # task_id -> {dep task_ids}
    dependents: dict[str, set[str]] = field(default_factory=dict)  # task_id -> {children}
    status: dict[str, str] = field(default_factory=dict)  # task_id -> completed|failed|pending

    def add(self, task_id: str, task: dict[str, Any], deps: list[str] | None = None) -> None:
        self.tasks[task_id] = task
        self.status.setdefault(task_id, "pending")
        self.dependencies.setdefault(task_id, set(deps or []))
        self.dependents.setdefault(task_id, set())
        for d in deps or []:
            self.dependents.setdefault(d, set()).add(task_id)

    def ready_tasks(self) -> list[str]:
        """Return ids of pending tasks whose deps are all completed."""
        ready = []
        for tid, deps in self.dependencies.items():
            if self.status.get(tid) != "pending":
                continue
            if all(self.status.get(d) == "completed" for d in deps):
                ready.append(tid)
        return ready

    def mark_completed(self, task_id: str) -> list[str]:
        """Mark a task done and return newly-unblocked dependent task ids."""
        self.status[task_id] = "completed"
        newly = []
        for child in self.dependents.get(task_id, set()):
            deps = self.dependencies.get(child, set())
            if self.status.get(child) == "pending" and all(
                self.status.get(d) == "completed" for d in deps
            ):
                newly.append(child)
        return newly

    def mark_failed(self, task_id: str) -> None:
        self.status[task_id] = "failed"

    def roots(self) -> list[str]:
        return [tid for tid, deps in self.dependencies.items() if not deps]

    def is_leaf(self, task_id: str) -> bool:
        return not self.dependents.get(task_id)


def build_dag(nodes: list[dict[str, Any]]) -> TaskDAG:
    """Build a TaskDAG from a list of node descriptors.

    Each node: {"task_id": str, "task": dict, "deps": [task_id, ...]}
    """
    dag = TaskDAG()
    for node in nodes:
        dag.add(node["task_id"], node["task"], node.get("deps"))
    return dag


def resolve_ready(dag: TaskDAG) -> list[dict[str, Any]]:
    """Return the task payloads of all currently-ready tasks."""
    return [dag.tasks[tid] for tid in dag.ready_tasks()]