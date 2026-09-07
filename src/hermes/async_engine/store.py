"""Task store for the async engine — PostgreSQL or SQLite, same interface.

Tables (spec §8 + idempotency §7):
  workflows         workflow aggregate status
  tasks             canonical task + live status/attempt/worker
  task_results      result_uri / result_hash (audit + evidence)
  execution_state   task_id -> execution state (idempotency: "completed" rows
                    are never re-executed even if a message is re-delivered)
  idempotency_keys  key -> task_id mapping for deduplication

Postgres when `dsn` given, else SQLite (local default).
"""
from __future__ import annotations

import hashlib
import json
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
    payload TEXT, metadata TEXT,
    idempotency_key TEXT, execution_state TEXT, resumed_from TEXT
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

# HERMES-05: DB-level idempotency — one result row per (task_id, status).
# A retried/redelivered task UPSERTS its result; duplicates are impossible
# at the storage layer, not just in application code.
_DDL_TASK_RESULTS_UX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_task_results_task_status "
    "ON task_results (task_id, status)"
)

_UPSERT_TASK_RESULT = """
INSERT INTO task_results (task_id, status, result_uri, result_hash, created_at)
VALUES (?,?,?,?,?)
ON CONFLICT(task_id, status) DO UPDATE SET
    result_uri=excluded.result_uri,
    result_hash=excluded.result_hash,
    created_at=excluded.created_at
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

_DDL_IDEMPOTENCY_KEYS = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    task_id TEXT,
    created_at TEXT
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
            self._connect = lambda: sqlite3.connect(db_path, timeout=30.0)

    def init(self) -> None:
        con = self._connect()
        if not self.dsn:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            con.executescript(
                _DDL_TASKS + ";" + _DDL_TASK_RESULTS + ";"
                + _DDL_WORKFLOWS + ";" + _DDL_EXEC_STATE + ";" + _DDL_TASK_DEPS + ";" + _DDL_IDEMPOTENCY_KEYS + ";"
            )
            con.execute(_DDL_TASK_RESULTS_UX)
        else:
            for ddl in (_DDL_TASKS, _DDL_TASK_RESULTS_PG, _DDL_WORKFLOWS,
                        _DDL_EXEC_STATE, _DDL_TASK_DEPS, _DDL_IDEMPOTENCY_KEYS):
                con.execute(ddl)
            con.execute(_DDL_TASK_RESULTS_UX)
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

    def _exec_count(self, sql: str, params: tuple = ()) -> int:
        """Execute a statement and return the number of rows affected.

        Used for atomic compare-and-set claims (e.g. mark_started) so a
        conditional UPDATE can report whether THIS call won the row.
        """
        con = self.backend._connect()
        cur = con.cursor()
        if self.dsn:
            cur = con.cursor()
            sql = sql.replace("?", "%s")
        cur.execute(sql, params)
        n = cur.rowcount
        con.commit()
        con.close()
        return n

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
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task.task_id, task.workflow_id, task.parent_task_id, task.task_type,
             task.priority, task.attempt, task.max_attempts, task.status.value,
             "", "", task.created_at, "", "", task.deadline,
             _dumps(task.payload), _dumps(task.metadata),
             task.idempotency_key or "", _dumps(task.execution_state), task.resumed_from or ""),
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
            idempotency_key=d.get("idempotency_key") or None,
            execution_state=_loads(d.get("execution_state") or "{}"),
            resumed_from=d.get("resumed_from") or None,
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

    def fail_tasks_with_failed_deps(self, limit: int = 200) -> list[str]:
        """HERMES-06: terminate queued tasks whose upstream dependency already
        failed — the cross-process advancer's deadlock guard. Returns the ids
        it failed."""
        sql = (
            "SELECT t.task_id FROM tasks t WHERE t.status = ? AND EXISTS ("
            "  SELECT 1 FROM task_dependencies d"
            "  JOIN tasks dt ON dt.task_id = d.depends_on"
            "  WHERE d.task_id = t.task_id AND dt.status = ?"
            ") LIMIT ?"
        )
        rows = self._exec(sql, (TaskStatus.QUEUED.value, TaskStatus.FAILED.value, limit),
                          fetch="all") or []
        failed = []
        for r in rows:
            tid = dict(r)["task_id"]
            self.mark_failed(tid, "upstream dependency failed — DAG stuck-task sweep",
                             worker_id="advancer")
            failed.append(tid)
        return failed

    def finalize_workflows(self) -> list[str]:
        """Mark 'running' workflows completed/failed once every task is terminal.
        Returns the list of finalized workflow ids."""
        rows = self._exec(
            "SELECT w.id AS id, COUNT(t.task_id) AS total, "
            "SUM(CASE WHEN t.status = ? THEN 1 ELSE 0 END) AS done, "
            "SUM(CASE WHEN t.status = ? THEN 1 ELSE 0 END) AS failed, "
            "SUM(CASE WHEN t.status = ? THEN 1 ELSE 0 END) AS partial "
            "FROM workflows w JOIN tasks t ON t.workflow_id = w.id "
            "WHERE w.status = 'running' GROUP BY w.id",
            (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.PARTIAL.value), fetch="all",
        ) or []
        finalized = []
        for r in rows:
            d = dict(r)
            if not d["total"]:
                continue
            failed_count = d["failed"]
            partial_count = d["partial"]
            completed_count = d["done"]
            status = "failed" if failed_count else (
                "completed" if (completed_count + partial_count) == d["total"] else None)
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
        already completed (idempotency) or already STARTED by another worker.

        HERMES-04-FU1: the claim is a single conditional UPDATE — the
        compare-and-set happens inside the statement, so two consumers racing
        on the same task_id cannot both win. Exactly one gets rowcount==1; the
        loser observes the already-running/completed row (rowcount==0) and
        must not execute.
        """
        n = self._exec_count(
            "UPDATE execution_state SET state=?, updated_at=? "
            "WHERE task_id=? AND state NOT IN (?,?)",
            (TaskStatus.RUNNING.value, _now(), task_id,
             TaskStatus.COMPLETED.value, TaskStatus.RUNNING.value),
        )
        if n != 1:
            return False  # lost the race, or already completed
        self._exec("UPDATE tasks SET status=?, worker_id=?, started_at=COALESCE(?, started_at) "
                   "WHERE task_id=?",
                   (TaskStatus.RUNNING.value, worker_id, _now(), task_id))
        return True

    def mark_completed(self, task_id: str, result_uri: str = "", result_hash: str = "",
                       worker_id: str = "") -> None:
        at = _now()
        self._exec("UPDATE execution_state SET state=?, updated_at=? WHERE task_id=?",
                   (TaskStatus.COMPLETED.value, at, task_id))
        self._exec("UPDATE tasks SET status=?, worker_id=?, completed_at=? WHERE task_id=?",
                   (TaskStatus.COMPLETED.value, worker_id, at, task_id))
        self._exec(
            _UPSERT_TASK_RESULT,
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
            _UPSERT_TASK_RESULT,
            (task_id, "failed", "", _hash(error), at),
        )

    def mark_retried(self, task_id: str, attempt: int, worker_id: str = "") -> None:
        self._exec("UPDATE execution_state SET state=?, attempt=?, updated_at=? "
                   "WHERE task_id=?",
                   (TaskStatus.RETRYING.value, attempt, _now(), task_id))
        self._exec("UPDATE tasks SET status=?, worker_id=?, attempt=? WHERE task_id=?",
                   (TaskStatus.RETRYING.value, worker_id, attempt, task_id))

    def mark_dispatched(self, task_id: str) -> None:
        """Advancer bookkeeping: message is on the bus, awaiting a worker claim
        (prevents the advancer from re-publishing the same task)."""
        self._exec("UPDATE execution_state SET state=?, updated_at=? WHERE task_id=?",
                   (TaskStatus.QUEUED.value, _now(), task_id))

    # ---- idempotency key methods ----
    def store_idempotency_key(self, key: str, task_id: str) -> bool:
        """Record a processed idempotency key. Returns True if newly stored, False if already exists."""
        try:
            self._exec(
                "INSERT INTO idempotency_keys (idempotency_key, task_id, created_at) VALUES (?,?,?)",
                (key, task_id, _now()),
            )
            return True
        except Exception:
            return False

    def check_idempotency_key(self, key: str) -> str | None:
        """Check if an idempotency key was already processed. Returns task_id if found."""
        row = self._exec("SELECT task_id FROM idempotency_keys WHERE idempotency_key=?",
                         (key,), fetch="one")
        return row["task_id"] if row else None

    def get_tasks_for_resume(self, workflow_id: str) -> list[Task]:
        """Returns tasks with execution_state for resume capability."""
        rows = self._exec(
            "SELECT * FROM tasks WHERE workflow_id=? AND status IN (?,?,?,?) ORDER BY priority",
            (workflow_id, TaskStatus.RUNNING.value, TaskStatus.PARTIAL.value,
             TaskStatus.BLOCKED.value, TaskStatus.RETRYING.value), fetch="all") or []
        return [self._row_to_task(dict(r)) for r in rows]

    def update_task_execution_state(self, task_id: str, state: dict) -> None:
        """Persist execution_state for resume capability."""
        self._exec(
            "UPDATE tasks SET execution_state=? WHERE task_id=?",
            (_dumps(state), task_id),
        )
        # Also update execution_state table
        attempt = state.get("attempt", 1)
        current_status = state.get("status", TaskStatus.RUNNING.value)
        self._exec("UPDATE execution_state SET state=?, attempt=?, updated_at=? WHERE task_id=?",
                   (current_status, attempt, _now(), task_id))

    def mark_task_partial(self, task_id: str, partial_results: dict) -> None:
        """Set status=PARTIAL with partial results in execution_state."""
        task = self.get_task(task_id)
        exec_state = task.execution_state.copy()
        exec_state["partial_results"] = partial_results
        exec_state["status"] = TaskStatus.PARTIAL.value
        self._exec(
            "UPDATE tasks SET status=?, execution_state=? WHERE task_id=?",
            (TaskStatus.PARTIAL.value, _dumps(exec_state), task_id),
        )
        self._exec("UPDATE execution_state SET state=?, updated_at=? WHERE task_id=?",
                   (TaskStatus.PARTIAL.value, _now(), task_id))

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
    return json.dumps(o, default=str)


def _loads(s: str) -> Any:
    try:
        return json.loads(s)
    except Exception:
        return {}


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
