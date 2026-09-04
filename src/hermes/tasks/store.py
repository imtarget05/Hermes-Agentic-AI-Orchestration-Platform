"""SQLite store: tasks table + append-only events table."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .schemas import Task, TaskEvent, TaskStatus, validate_transition


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def init_db(db_path: str) -> None:
    con = _connect(db_path)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, text TEXT, project TEXT, strategy TEXT,
            status TEXT, owner_agent TEXT, retries INTEGER, max_retries INTEGER,
            result TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, frm TEXT,
            [to] TEXT, actor TEXT, note TEXT, at TEXT
        );
        """
    )
    con.commit()
    con.close()


class TaskStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_db(db_path)

    def create(self, task: Task) -> Task:
        con = _connect(self.db_path)
        con.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (task.id, task.text, task.project, task.strategy, task.status.value,
             task.owner_agent, task.retries, task.max_retries, task.result,
             task.created_at, task.updated_at),
        )
        con.commit()
        con.close()
        self.log(task.id, TaskStatus.CREATED.value, TaskStatus.QUEUED.value, "system", "enqueued")
        # auto created→queued
        self.transition(task.id, TaskStatus.QUEUED, "system")
        return self.get(task.id)

    def get(self, task_id: str) -> Task:
        con = _connect(self.db_path)
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        con.close()
        if not row:
            raise KeyError(f"task {task_id} not found")
        d = dict(row)
        d["status"] = TaskStatus(d["status"])
        return Task(**d)

    def transition(self, task_id: str, to: TaskStatus, actor: str, note: str = "") -> Task:
        task = self.get(task_id)
        validate_transition(task.status, to)
        frm = task.status
        task.status = to
        task.updated_at = datetime.now(timezone.utc).isoformat()
        if to == TaskStatus.RETRY:
            task.retries += 1
        con = _connect(self.db_path)
        con.execute(
            "UPDATE tasks SET status=?, owner_agent=?, retries=?, result=?, updated_at=? WHERE id=?",
            (task.status.value, task.owner_agent, task.retries, task.result, task.updated_at, task.id),
        )
        con.commit()
        con.close()
        self.log(task_id, frm.value, to.value, actor, note)
        return self.get(task_id)

    def set_result(self, task_id: str, result: str, owner: str = "") -> Task:
        task = self.get(task_id)
        task.result = result
        if owner:
            task.owner_agent = owner
        task.updated_at = datetime.now(timezone.utc).isoformat()
        con = _connect(self.db_path)
        con.execute(
            "UPDATE tasks SET result=?, owner_agent=?, updated_at=? WHERE id=?",
            (task.result, task.owner_agent, task.updated_at, task.id),
        )
        con.commit()
        con.close()
        return self.get(task_id)

    def set_owner(self, task_id: str, owner: str) -> Task:
        task = self.get(task_id)
        con = _connect(self.db_path)
        con.execute("UPDATE tasks SET owner_agent=? WHERE id=?", (owner, task_id))
        con.commit()
        con.close()
        return self.get(task_id)

    def log(self, task_id: str, frm: str, to: str, actor: str, note: str = "") -> None:
        con = _connect(self.db_path)
        ev = TaskEvent(task_id=task_id, frm=frm, to=to, actor=actor, note=note)
        con.execute(
            "INSERT INTO events (task_id, frm, [to], actor, note, at) VALUES (?,?,?,?,?,?)",
            (ev.task_id, ev.frm, ev.to, ev.actor, ev.note, ev.at),
        )
        con.commit()
        con.close()

    def events(self, task_id: str) -> list[dict]:
        con = _connect(self.db_path)
        rows = con.execute("SELECT * FROM events WHERE task_id=? ORDER BY rowid", (task_id,)).fetchall()
        con.close()
        return [dict(r) for r in rows]

    def list_tasks(self, limit: int = 20) -> list[dict]:
        con = _connect(self.db_path)
        rows = con.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        con.close()
        return [dict(r) for r in rows]

    def export_json(self, task_id: str) -> str:
        return json.dumps({"task": self.get(task_id).model_dump(), "events": self.events(task_id)}, indent=2)
