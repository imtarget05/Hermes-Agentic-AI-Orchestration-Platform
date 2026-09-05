"""Canonical Task contract, workflow, statuses, and routing registry.

Every task has one canonical structure. No worker invents its own format.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_MAX_ATTEMPTS = 3
EXCHANGE_TASKS = "hermes.tasks"
EXCHANGE_RETRY = "hermes.retry"
EXCHANGE_DLX = "hermes.dlx"

# Kafka lifecycle event types (event_type values).
EVENT_CREATED = "task.created"
EVENT_STARTED = "task.started"
EVENT_COMPLETED = "task.completed"
EVENT_FAILED = "task.failed"
EVENT_RETRIED = "task.retried"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}" if prefix else uuid.uuid4().hex


class TaskStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    STARTED = "started"
    COMPLETED = "completed"
    RETRY = "retry"
    FAILED = "failed"


# lifecycle for the async engine (subset, sufficient for idempotency + audit).
ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.QUEUED},
    TaskStatus.QUEUED: {TaskStatus.STARTED, TaskStatus.FAILED},
    TaskStatus.STARTED: {TaskStatus.COMPLETED, TaskStatus.RETRY, TaskStatus.FAILED},
    TaskStatus.RETRY: {TaskStatus.STARTED, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
}


def validate_transition(frm: TaskStatus, to: TaskStatus) -> None:
    if to not in ALLOWED_TRANSITIONS[frm]:
        raise ValueError(f"Illegal transition {frm.value} -> {to.value}")


class Task(BaseModel):
    """Canonical task contract (see spec §4)."""

    task_id: str = Field(default_factory=new_id)
    workflow_id: str = ""
    parent_task_id: str | None = None
    task_type: str
    priority: int = 5
    attempt: int = 1
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    created_at: str = Field(default_factory=_now)
    deadline: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: TaskStatus = TaskStatus.CREATED

    def to_message(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_message(cls, msg: dict[str, Any]) -> Task:
        msg = dict(msg)
        msg["status"] = TaskStatus(msg.get("status", "created"))
        return cls(**msg)


class TaskResult(BaseModel):
    task_id: str
    status: str
    result_uri: str = ""
    result_hash: str = ""
    created_at: str = Field(default_factory=_now)


class Workflow(BaseModel):
    id: str = Field(default_factory=new_id)
    status: str = "running"  # running | completed | failed
    created_at: str = Field(default_factory=_now)
    completed_at: str = ""

    def done(self, status: str = "completed") -> None:
        self.status = status
        self.completed_at = _now()


# task_type -> (exchange, routing_key, queue). Queues match spec §3.
def routing_for(task_type: str) -> tuple[str, str, str]:
    """Return (exchange, routing_key, queue) for a task type."""
    if task_type not in ROUTING:
        raise KeyError(f"unknown task_type: {task_type}")
    return ROUTING[task_type]


ROUTING: dict[str, tuple[str, str, str]] = {
    "research": (EXCHANGE_TASKS, "agent.research", "q.agent.research"),
    "analyze": (EXCHANGE_TASKS, "agent.analyze", "q.agent.analyze"),
    "report": (EXCHANGE_TASKS, "agent.report", "q.agent.report"),
    "notify": (EXCHANGE_TASKS, "agent.notify", "q.agent.notify"),
    # Enterprise Procurement Case Agent — 4 parallel + join + verify
    "price": (EXCHANGE_TASKS, "agent.price", "q.agent.price"),
    "vendor": (EXCHANGE_TASKS, "agent.vendor", "q.agent.vendor"),
    "contract": (EXCHANGE_TASKS, "agent.contract", "q.agent.contract"),
    "spec": (EXCHANGE_TASKS, "agent.spec", "q.agent.spec"),
    "analysis": (EXCHANGE_TASKS, "agent.analysis", "q.agent.analysis"),
    "verification": (EXCHANGE_TASKS, "agent.verification", "q.agent.verification"),
}

PROCUREMENT_TASK_TYPES = ("price", "vendor", "contract", "spec", "analysis", "verification")

DEAD_LETTER_QUEUE = "q.agent.deadletter"