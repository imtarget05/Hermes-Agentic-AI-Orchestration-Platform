"""Dashboard / Inbox API — Phase 3 (§9). Read-only over TaskStore."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .config import settings
from .tasks import TaskStore

app = FastAPI(title="Hermes Inbox")


def _store() -> TaskStore:
    return TaskStore(settings.hermes_db_path, dsn=settings.hermes_database_url or None)


@app.get("/tasks")
def list_tasks(limit: int = 20):
    return _store().list_tasks(limit)


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    try:
        s = _store()
        return {"task": s.get(task_id).model_dump(), "events": s.events(task_id)}
    except KeyError:
        raise HTTPException(404, "task not found")
