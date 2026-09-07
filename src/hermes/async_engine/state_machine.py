"""DAG State Machine — validates transitions, computes next status, enables resume."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .contract import Task, TaskStatus

if TYPE_CHECKING:
    from .dag import TaskDAG


class DAGStateMachine:
    """State machine for DAG task execution with idempotency and resume support."""

    ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
        TaskStatus.CREATED: {TaskStatus.QUEUED, TaskStatus.CANCELLED},
        TaskStatus.QUEUED: {
            TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.BLOCKED,
            TaskStatus.CANCELLED, TaskStatus.DEADLETTER,
        },
        TaskStatus.RUNNING: {
            TaskStatus.COMPLETED,
            TaskStatus.RETRYING,
            TaskStatus.FAILED,
            TaskStatus.PARTIAL,
            TaskStatus.BLOCKED,
            TaskStatus.TIMEOUT,
            TaskStatus.CANCELLED,
        },
        TaskStatus.RETRYING: {
            TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED,
        },
        TaskStatus.PARTIAL: {
            TaskStatus.COMPLETED, TaskStatus.RETRYING, TaskStatus.FAILED,
            TaskStatus.BLOCKED, TaskStatus.CANCELLED,
        },
        TaskStatus.BLOCKED: {
            TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.COMPLETED, TaskStatus.CANCELLED,
        },
        TaskStatus.TIMEOUT: {TaskStatus.RETRYING, TaskStatus.FAILED, TaskStatus.DEADLETTER},
        TaskStatus.COMPLETED: set(),
        TaskStatus.FAILED: {TaskStatus.RETRYING, TaskStatus.DEADLETTER},
        TaskStatus.CANCELLED: set(),
        TaskStatus.DEADLETTER: set(),
    }

    EVENT_TRANSITIONS: dict[TaskStatus, dict[str, TaskStatus]] = {
        TaskStatus.CREATED: {"dispatch": TaskStatus.QUEUED},
        TaskStatus.QUEUED: {"start": TaskStatus.RUNNING, "fail": TaskStatus.FAILED, "block": TaskStatus.BLOCKED},
        TaskStatus.RUNNING: {
            "complete": TaskStatus.COMPLETED,
            "fail": TaskStatus.FAILED,
            "retry": TaskStatus.RETRYING,
            "partial": TaskStatus.PARTIAL,
            "block": TaskStatus.BLOCKED,
        },
        TaskStatus.RETRYING: {"start": TaskStatus.RUNNING, "fail": TaskStatus.FAILED, "block": TaskStatus.BLOCKED},
        TaskStatus.PARTIAL: {
            "complete": TaskStatus.COMPLETED,
            "fail": TaskStatus.FAILED,
            "retry": TaskStatus.RETRYING,
            "block": TaskStatus.BLOCKED,
        },
        TaskStatus.BLOCKED: {"unblock": TaskStatus.RUNNING, "fail": TaskStatus.FAILED, "complete": TaskStatus.COMPLETED},
        TaskStatus.COMPLETED: {},
        TaskStatus.FAILED: {"retry": TaskStatus.RETRYING},
    }

    @classmethod
    def validate_transition(cls, from_status: TaskStatus, to_status: TaskStatus) -> None:
        """Validate that a transition is allowed."""
        if to_status not in cls.ALLOWED_TRANSITIONS[from_status]:
            raise ValueError(f"Illegal transition {from_status.value} -> {to_status.value}")

    @classmethod
    def get_next_status(cls, current_status: TaskStatus, event: str) -> TaskStatus:
        """Get the next status based on current status and event."""
        transitions = cls.EVENT_TRANSITIONS.get(current_status, {})
        if event not in transitions:
            raise ValueError(f"Event '{event}' not valid from status {current_status.value}")
        return transitions[event]

    @classmethod
    def can_resume(cls, task: Task) -> bool:
        """Check if a task can be resumed from its execution_state."""
        if not task.execution_state:
            return False
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return False
        attempt = task.execution_state.get("attempt", 1)
        if attempt >= task.max_attempts:
            return False
        return True

    @classmethod
    def compute_partial_dag(
        cls, dag: "TaskDAG", completed_tasks: set[str], failed_tasks: set[str]
    ) -> "TaskDAG":
        """Compute a subgraph of tasks that can continue execution.

        Returns a new TaskDAG containing only tasks that are not completed/failed
        and whose dependencies are either completed or in the subgraph.
        """
        from .dag import TaskDAG

        all_tasks = set(dag.tasks.keys())
        blocked = completed_tasks | failed_tasks
        available = all_tasks - blocked

        new_dag = TaskDAG()
        for task_id in available:
            task = dag.tasks[task_id]
            deps = [d for d in dag.dependencies.get(task_id, set()) if d in available]
            new_dag.add(task_id, task, deps)

        for task_id in completed_tasks:
            if task_id in dag.tasks:
                new_dag.add(task_id, dag.tasks[task_id], list(dag.dependencies.get(task_id, set())))
                new_dag.status[task_id] = "completed"

        for task_id in failed_tasks:
            if task_id in dag.tasks:
                new_dag.add(task_id, dag.tasks[task_id], list(dag.dependencies.get(task_id, set())))
                new_dag.status[task_id] = "failed"

        return new_dag

    @classmethod
    def get_resumable_tasks(cls, dag: "TaskDAG", store) -> list[Task]:
        """Get tasks that can be resumed from the store."""
        resumable = []
        for task_id, status in dag.status.items():
            if status in ("pending", "running", "partial"):
                task = store.get_task(task_id)
                if cls.can_resume(task):
                    resumable.append(task)
        return resumable
