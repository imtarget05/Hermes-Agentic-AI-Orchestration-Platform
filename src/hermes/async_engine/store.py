"""Task store for the async engine — PostgreSQL or SQLite, same interface.

Tables (spec §8 + idempotency §7):
  workflows         workflow aggregate status
  tasks             canonical task + live status/attempt/worker
  task_results      result_uri / result_hash (audit + evidence)
  execution_state   task_id -> execution state (idempotency: "completed" rows
                    are never re-executed even if a message is re-delivered)

Postgres when `dsn` given, else SQLite (local default).
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contract import Task, TaskStatus, Workflow

_DDL_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    workflow_id TEXT, parent_task_id TEXT, task_type TEXT,
    priority INTEGER, attempt INTEGER, max_attempts INTEGER,
    status TEXT, error TEXT, worker_id TEXT,
    created_at TEXT, started_at TEXT, completed_at TEXT, deadline TEXT,
    payload TEXT, metadata TEXT
)
"""

_DDL_TASK_RESULTS = """
CREATE TABLE IF NOT EXISTS task_results (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT, status TEXT, result_uri TEXT, result_hash TEXT, created_at TEXT
)
"""
_DDL_TASK_RESULTS_PG = """
CREATE TABLE IF NOT EXISTS task_results (
    rowid BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id TEXT, status TEXT, result_uri TEXT, result_hash TEXT, created_at TEXT
)
"""

_DDL_WORKFLOWS = """
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY, status TEXT, created_at TEXT, completed_at TEXT
)
"""

_DDL_EXEC_STATE = """
CREATE TABLE IF NOT EXISTS execution_state (
    task_id TEXT PRIMARY KEY, state TEXT, attempt INTEGER, updated_at TEXT
)
"""

_DDL_TASK_DEPS = """
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id TEXT, depends_on TEXT, PRIMARY KEY (task_id, depends_on)
)
"""


class _Backend:
    """Wrapper unifying sqlite3 and psycopg3 connections."""

    def __init__(self, db_path: str = "", dsn: str = ""):
        self.dsn = dsn
        self.db_path = db_path
        if dsn:
            import psycopg
            self._connect = lambda: psycopg.connect(dsn)
        else:
            self._connect = lambda: sqlite3.connect(db_path)

    def init(self) -> None:
        con = self._connect()
        if not self.dsn:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            con.executescript(
                _DDL_TASKS + ";" + _DDL_TASK_RESULTS + ";"
                + _DDL_WORKFLOWS + ";" + _DDL_EXEC_STATE + ";" + _DDL_TASK_DEPS + ";"
            )
        else:
            for ddl in (_DDL_TASKS, _DDL_TASK_RESULTS_PG, _DDL_WORKFLOWS,
                        _DDL_EXEC_STATE, _DDL_TASK_DEPS):
                con.execute(ddl)
        con.commit()
        con.close()


class AsyncTaskStore:
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

    # ---- workflows ----
    def create_workflow(self, workflow_id: str) -> Workflow:
        wf = Workflow(id=workflow_id)
        self._exec(
            "INSERT INTO workflows (id, status, created_at, completed_at) VALUES (?,?,?,?)",
            (wf.id, wf.status, wf.created_at, wf.completed_at),
        )
        return wf

    def get_workflow(self, workflow_id: str) -> Workflow:
        row = self._exec("SELECT * FROM workflows WHERE id=?", (workflow_id,), fetch="one")
        if not row:
            raise KeyError(f"workflow {workflow_id} not found")
        return Workflow(**dict(row))

    def workflow_status(self, workflow_id: str) -> str:
        return self.get_workflow(workflow_id).status

    def complete_workflow(self, workflow_id: str, status: str = "completed") -> Workflow:
        at = _now()
        self._exec("UPDATE workflows SET status=?, completed_at=? WHERE id=?",
                   (status, at, workflow_id))
# ---- tasks ----
    def create_task(self, task: Task) -> Task:
        self._exec(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task.task_id, task.workflow_id, task.parent_task_id, task.task_type,
             task.priority, task.attempt, task.max_attempts, task.status.value,
             "", "", task.created_at, "", "", task.deadline,
             _dumps(task.payload), _dumps(task.metadata)),
        )
        self._exec(
            "INSERT OR REPLACE INTO execution_state (task_id, state, attempt, updated_at) "
            "VALUES (?,?,?,?)",
            (task.task_id, TaskStatus.CREATED.value, task.attempt, _now()),
        )
        return task

    def get_task(self, task_id: str) -> Task:
        row = self._exec("SELECT * FROM tasks WHERE task_id=?", (task_id,), fetch="one")
        if not row:
            raise KeyError(f"task {task_id} not found")
        return self._row_to_task(dict(row))

    def _row_to_task(self, d: dict) -> Task:
        return Task(
            task_id=d["task_id"], workflow_id=d["workflow_id"],
            parent_task_id=d.get("parent_task_id"), task_type=d["task_type"],
            priority=d["priority"], attempt=d["attempt"], max_attempts=d["max_attempts"],
            status=TaskStatus(d["status"]), created_at=d["created_at"],
            deadline=d["deadline"] or "", payload=_loads(d["payload"] or "{}"),
            metadata=_loads(d["metadata"] or "{}"),
        )

    def list_workflow_tasks(self, workflow_id: str) -> list[Task]:
        rows = self._exec("SELECT * FROM tasks WHERE workflow_id=? ORDER BY priority",
                          (workflow_id,), fetch="all") or []
        return [self._row_to_task(dict(r)) for r in rows]

    def set_status(self, task_id: str, status: TaskStatus, error: str = "",
                   worker_id: str = "") -> Task:
        self._exec("UPDATE tasks SET status=?, error=?, worker_id=? WHERE task_id=?",
                   (status.value, error[:300], worker_id, task_id))
        return self.get_task(task_id)

    def set_attempt(self, task_id: str, attempt: int) -> None:
        self._exec("UPDATE tasks SET attempt=? WHERE task_id=?", (attempt, task_id))

    # ---- DAG dependencies (cross-process dispatch support) ----
    def add_dependencies(self, task_id: str, deps: list[str]) -> None:
        for dep in deps or []:
            self._exec("INSERT OR IGNORE INTO task_dependencies (task_id, depends_on) "
                       "VALUES (?,?)", (task_id, dep))

    def get_dependencies(self, task_id: str) -> list[str]:
        rows = self._exec("SELECT depends_on FROM task_dependencies WHERE task_id=?",
                          (task_id,), fetch="all") or []
        return [r["depends_on"] for r in rows]

    def dispatchable_tasks(self, limit: int = 50) -> list[Task]:
        """Tasks not yet claimed whose deps are all COMPLETED — the DAG advancer
        publishes these to the bus (works across separate processes)."""
        sql = (
            "SELECT t.* FROM tasks t "
            "JOIN execution_state es ON es.task_id = t.task_id AND es.state = ? "
            "WHERE t.status = ? AND NOT EXISTS ("
            "  SELECT 1 FROM task_dependencies d"
            "  JOIN tasks dt ON dt.task_id = d.depends_on"
            "  WHERE d.task_id = t.task_id AND dt.status != ?"
            ") ORDER BY t.priority, t.created_at LIMIT ?"
        )
        rows = self._exec(sql, (TaskStatus.CREATED.value, TaskStatus.QUEUED.value,
                                TaskStatus.COMPLETED.value, limit), fetch="all") or []
        return [self._row_to_task(dict(r)) for r in rows]

    def finalize_workflows(self) -> list[str]:
        """Mark 'running' workflows completed/failed once every task is terminal.
        Returns the list of finalized workflow ids."""
        rows = self._exec(
            "SELECT w.id AS id, COUNT(t.task_id) AS total, "
            "SUM(CASE WHEN t.status = ? THEN 1 ELSE 0 END) AS done, "
            "SUM(CASE WHEN t.status = ? THEN 1 ELSE 0 END) AS failed "
            "FROM workflows w JOIN tasks t ON t.workflow_id = w.id "
            "WHERE w.status = 'running' GROUP BY w.id",
            (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value), fetch="all",
        ) or []
        finalized = []
        for r in rows:
            d = dict(r)
            if not d["total"]:
                continue
            status = "failed" if d["failed"] else (
                "completed" if d["done"] == d["total"] else None)
            if status:
                self.complete_workflow(d["id"], status)
                finalized.append(d["id"])
        return finalized
# ---- idempotency / execution state ----
    def execution_state(self, task_id: str) -> str | None:
        row = self._exec("SELECT state FROM execution_state WHERE task_id=?",
                         (task_id,), fetch="one")
        return row["state"] if row else None

    def is_completed(self, task_id: str) -> bool:
        return self.execution_state(task_id) == TaskStatus.COMPLETED.value

    def mark_started(self, task_id: str, worker_id: str) -> bool:
        """Atomic claim: True if this worker may execute. False if the task is
        already completed (idempotency) or already STARTED by another worker."""
        state = self.execution_state(task_id)
        if state in (TaskStatus.COMPLETED.value, TaskStatus.STARTED.value):
            return False
        self._exec("UPDATE execution_state SET state=?, updated_at=? WHERE task_id=?",
                   (TaskStatus.STARTED.value, _now(), task_id))
        self._exec("UPDATE tasks SET status=?, worker_id=?, started_at=COALESCE(?, started_at) "
                   "WHERE task_id=?",
                   (TaskStatus.STARTED.value, worker_id, _now(), task_id))
        return True

    def mark_completed(self, task_id: str, result_uri: str = "", result_hash: str = "",
                       worker_id: str = "") -> None:
        at = _now()
        self._exec("UPDATE execution_state SET state=?, updated_at=? WHERE task_id=?",
                   (TaskStatus.COMPLETED.value, at, task_id))
        self._exec("UPDATE tasks SET status=?, worker_id=?, completed_at=? WHERE task_id=?",
                   (TaskStatus.COMPLETED.value, worker_id, at, task_id))
        self._exec(
            "INSERT INTO task_results (task_id, status, result_uri, result_hash, created_at) "
            "VALUES (?,?,?,?,?)",
            (task_id, "completed", result_uri, result_hash or _hash(result_uri), at),
        )

    def mark_failed(self, task_id: str, error: str, worker_id: str = "") -> None:
        at = _now()
        self._exec("UPDATE execution_state SET state=?, updated_at=? WHERE task_id=?",
                   (TaskStatus.FAILED.value, at, task_id))
        self._exec("UPDATE tasks SET status=?, error=?, worker_id=?, completed_at=? "
                   "WHERE task_id=?",
                   (TaskStatus.FAILED.value, error[:300], worker_id, at, task_id))
        self._exec(
            "INSERT INTO task_results (task_id, status, result_uri, result_hash, created_at) "
            "VALUES (?,?,?,?,?)",
            (task_id, "failed", "", _hash(error), at),
        )

    def mark_retried(self, task_id: str, attempt: int, worker_id: str = "") -> None:
        self._exec("UPDATE execution_state SET state=?, attempt=?, updated_at=? "
                   "WHERE task_id=?",
                   (TaskStatus.RETRY.value, attempt, _now(), task_id))
        self._exec("UPDATE tasks SET status=?, worker_id=?, attempt=? WHERE task_id=?",
                   (TaskStatus.RETRY.value, worker_id, attempt, task_id))

    def mark_dispatched(self, task_id: str) -> None:
        """Advancer bookkeeping: message is on the bus, awaiting a worker claim
        (prevents the advancer from re-publishing the same task)."""
        self._exec("UPDATE execution_state SET state=?, updated_at=? WHERE task_id=?",
                   (TaskStatus.QUEUED.value, _now(), task_id))

    def task_results(self, task_id: str) -> list[dict]:
        rows = self._exec("SELECT * FROM task_results WHERE task_id=? ORDER BY rowid",
                          (task_id,), fetch="all") or []
        return [dict(r) for r in rows]

    def list_tasks(self, limit: int = 100) -> list[dict]:
        rows = self._exec("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                          (limit,), fetch="all") or []
        return [dict(r) for r in rows]

    def task_counts(self) -> dict[str, int]:
        rows = self._exec("SELECT status, COUNT(*) AS c FROM tasks GROUP BY status",
                          fetch="all") or []
        return {dict(r)["status"]: dict(r)["c"] for r in rows}


def init_async_db(db_path: str, dsn: str | None = None) -> None:
    AsyncTaskStore(db_path, dsn=dsn)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dumps(o: Any) -> str:
    import json
    return json.dumps(o, default=str)


def _loads(s: str) -> Any:
    import json
    try:
        return json.loads(s)
    except Exception:
        return {}


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]