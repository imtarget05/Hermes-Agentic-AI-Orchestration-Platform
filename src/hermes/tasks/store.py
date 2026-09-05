"""Task store: SQLite (local) or Postgres (deployed) — same interface.

Backend selection: pass `dsn` explicitly, or set HERMES_DATABASE_URL env var.
When a DSN is present, psycopg3 is used; otherwise sqlite3 (default).
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .schemas import Task, TaskEvent, TaskStatus, validate_transition

_DDL_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, text TEXT, project TEXT, strategy TEXT,
    status TEXT, owner_agent TEXT, retries INTEGER, max_retries INTEGER,
    result TEXT, created_at TEXT, updated_at TEXT
)"""

_DDL_EVENTS_SQLITE = """
CREATE TABLE IF NOT EXISTS events (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, frm TEXT,
    "to" TEXT, actor TEXT, note TEXT, at TEXT
)"""

_DDL_EVENTS_PG = """
CREATE TABLE IF NOT EXISTS events (
    rowid BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, task_id TEXT, frm TEXT,
    "to" TEXT, actor TEXT, note TEXT, at TEXT
)"""


class _Backend:
    """Minimal wrapper unifying sqlite3 and psycopg3 connections."""

    def __init__(self, db_path: str = "", dsn: str = ""):
        self.dsn, self.db_path = dsn, db_path
        if dsn:
            import psycopg
            self._connect = lambda: psycopg.connect(dsn)
        else:
            self._connect = lambda: sqlite3.connect(db_path, timeout=30.0)

    def init(self) -> None:
        con = self._connect()
        if not self.dsn:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            con.executescript(_DDL_TASKS + ";" + _DDL_EVENTS_SQLITE + ";")
        else:
            for ddl in (_DDL_TASKS, _DDL_EVENTS_PG):
                con.execute(ddl)
        con.commit()
        con.close()



class TaskStore:
    def __init__(self, db_path: str, dsn: str | None = None):
        self.dsn = dsn if dsn is not None else os.environ.get("HERMES_DATABASE_URL", "")
        self.db_path = db_path
        self.backend = _Backend(db_path, self.dsn)
        self.backend.init()

    def _exec(self, sql: str, params: tuple = (), fetch: str = ""):
        con = self.backend._connect()
        cur = con.cursor()
        if self.dsn:
            from psycopg.rows import dict_row
            cur = con.cursor(row_factory=dict_row)
            sql = sql.replace("?", "%s")
        else:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
        cur.execute(sql, params)
        out = None
        if fetch == "one":
            out = cur.fetchone()
        elif fetch == "all":
            out = cur.fetchall()
        con.commit()
        con.close()
        return out

    def create(self, task: Task) -> Task:
        self._exec(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (task.id, task.text, task.project, task.strategy, task.status.value,
             task.owner_agent, task.retries, task.max_retries, task.result,
             task.created_at, task.updated_at),
        )
        self.log(task.id, TaskStatus.CREATED.value, TaskStatus.QUEUED.value, "system", "enqueued")
        # auto created→queued
        self.transition(task.id, TaskStatus.QUEUED, "system")
        return self.get(task.id)

    def get(self, task_id: str) -> Task:
        row = self._exec("SELECT * FROM tasks WHERE id=?", (task_id,), fetch="one")
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
        task.updated_at = datetime.now(UTC).isoformat()
        if to == TaskStatus.RETRY:
            task.retries += 1
        self._exec(
            "UPDATE tasks SET status=?, owner_agent=?, retries=?, result=?, updated_at=? WHERE id=?",
            (task.status.value, task.owner_agent, task.retries, task.result, task.updated_at, task.id),
        )
        self.log(task_id, frm.value, to.value, actor, note)
        return self.get(task_id)

    def set_result(self, task_id: str, result: str, owner: str = "") -> Task:
        task = self.get(task_id)
        task.result = result
        if owner:
            task.owner_agent = owner
        task.updated_at = datetime.now(UTC).isoformat()
        self._exec(
            "UPDATE tasks SET result=?, owner_agent=?, updated_at=? WHERE id=?",
            (task.result, task.owner_agent, task.updated_at, task.id),
        )
        return self.get(task_id)

    def set_owner(self, task_id: str, owner: str) -> Task:
        self._exec("UPDATE tasks SET owner_agent=? WHERE id=?", (owner, task_id))
        return self.get(task_id)

    def log(self, task_id: str, frm: str, to: str, actor: str, note: str = "") -> None:
        ev = TaskEvent(task_id=task_id, frm=frm, to=to, actor=actor, note=note)
        self._exec(
            'INSERT INTO events (task_id, frm, "to", actor, note, at) VALUES (?,?,?,?,?,?)',
            (ev.task_id, ev.frm, ev.to, ev.actor, ev.note, ev.at),
        )

    def events(self, task_id: str) -> list[dict]:
        rows = self._exec("SELECT * FROM events WHERE task_id=? ORDER BY rowid", (task_id,), fetch="all")
        return [dict(r) for r in rows or []]

    def list_tasks(self, limit: int = 20) -> list[dict]:
        rows = self._exec("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,), fetch="all")
        return [dict(r) for r in rows or []]

    def export_json(self, task_id: str) -> str:
        return json.dumps({"task": self.get(task_id).model_dump(), "events": self.events(task_id)}, indent=2)


def init_db(db_path: str, dsn: str | None = None) -> None:
    TaskStore(db_path, dsn=dsn)
