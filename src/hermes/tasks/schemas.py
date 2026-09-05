"""Task schemas + lifecycle state machine (§7 of spec)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    HANDOFF = "handoff"
    RETRY = "retry"
    COMPLETED = "completed"
    FAILURE = "failure"
    FAILED = "failed"


# created→queued→running→handoff→retry→completed ; running→failure→retry/failed
ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.QUEUED},
    TaskStatus.QUEUED: {TaskStatus.RUNNING},
    TaskStatus.RUNNING: {TaskStatus.HANDOFF, TaskStatus.FAILURE, TaskStatus.COMPLETED},
    TaskStatus.HANDOFF: {TaskStatus.RUNNING, TaskStatus.COMPLETED, TaskStatus.FAILURE},
    TaskStatus.FAILURE: {TaskStatus.RETRY, TaskStatus.FAILED},
    TaskStatus.RETRY: {TaskStatus.RUNNING, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
}


def validate_transition(frm: TaskStatus, to: TaskStatus) -> None:
    if to not in ALLOWED_TRANSITIONS[frm]:
        raise ValueError(f"Illegal transition {frm.value} → {to.value}")


class Task(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    text: str
    project: str = "default"
    strategy: str = "fanout"  # fanout | pipeline | critic
    status: TaskStatus = TaskStatus.CREATED
    owner_agent: str = "orchestrator"
    retries: int = 0
    max_retries: int = 3
    result: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class TaskEvent(BaseModel):
    task_id: str
    frm: str
    to: str
    actor: str
    note: str = ""
    at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
